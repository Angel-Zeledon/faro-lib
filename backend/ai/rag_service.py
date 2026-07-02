"""
RAG service — Voyage AI embeddings + Pinecone vector store + Anthropic generation.

Multi-tenant security model:
  Namespace  = tenant_id          → physical wall between companies (Pinecone)
  Filter     = session_id + user_id → row-level isolation within a tenant

Chunks indexed per session (natural-language text for good semantic similarity):
  dataset_profile  — rows, SKUs, freq, date range, warnings        (1 chunk)
  model_summary    — per-model aggregate MAE/RMSE/WAPE             (N chunks)
  sku_metrics      — per-SKU accuracy across all models            (M chunks)
  inventory        — reorder point, safety stock, stockout risk    (M chunks)
  data_quality     — quality score, series type, outliers          (M chunks)
  routing          — which models ran on which SKUs                (1 chunk)
  config           — features, horizon, validation strategy        (1 chunk)
  forecast_summary — per-SKU per-model trend (start→end, avg)     (M×K chunks)

Retrieval: semantic similarity; namespace + metadata filter (session_id + user_id).
Generation: Claude Sonnet 4.6, up to 6 history turns, max 1200 tokens.
Threshold: chunks with cosine score < MIN_SCORE are discarded before LLM call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

EMBED_MODEL   = "voyage-3"
EMBED_DIM     = 1024
UPSERT_BATCH  = 100    # Pinecone max per upsert call
EMBED_BATCH   = 96     # Voyage AI safe batch size
TOP_K_DEFAULT = 8
MAX_HISTORY   = 6      # message turns kept in context (3 user + 3 assistant)
MAX_TOKENS    = 1200
MIN_SCORE     = 0.30   # cosine similarity threshold — below this, query is off-topic

_SYSTEM_PROMPT = """\
You are an expert demand forecasting analyst embedded in an enterprise SaaS platform.
Your role: explain ML model outputs, surface actionable business insights, and help
business users understand their forecasts, inventory recommendations, and data quality.

Guidelines:
- Base every statement on the retrieved context provided. Do not invent numbers or SKU names.
- Be specific: cite actual values (MAE, WAPE, units, risk %) from the context.
- Use business language — say "forecast accuracy" not "MAE", "order quantity" not "safety stock"
  unless the user asks for technical details.
- Structure responses: use bullet points for lists, plain prose for explanations.
- If the context does not contain enough information to answer, say so explicitly.
- When comparing models, rank them by WAPE (lower is better) unless asked otherwise.
- Always close with one concrete, actionable recommendation.
"""

_OFF_TOPIC_REPLY = (
    "I can only answer questions about the forecast results, model accuracy, "
    "inventory recommendations, data quality for this session, or the uploaded documents. "
    "Your question doesn't appear to be related to the available data. "
    "Please ask about SKU performance, model comparisons, inventory actions, "
    "dataset quality, or the content of any uploaded documents."
)

_NO_ACCESS_REPLY = (
    "No information was found for this session under your user credentials. "
    "Either the session has not been indexed yet or you do not have access to "
    "the requested data. Please contact your administrator if you believe this is an error."
)


class RAGService:
    """
    Lazy-initialised RAG service. Fails gracefully when API keys are missing
    or packages are not installed, falling back to context-dump generation.
    """

    def __init__(self):
        self._voyage = None
        self._index  = None
        self._anthro = None
        self._ready: Optional[bool] = None  # None = not yet checked

    # ──────────────────────────────────────────────────────────────────
    # Init
    # ──────────────────────────────────────────────────────────────────

    def _init(self) -> bool:
        if self._ready is not None:
            return self._ready

        from backend.config import settings

        missing = []
        if not settings.voyageai_api_key:
            missing.append("VOYAGEAI_API_KEY")
        if not settings.pinecone_api_key:
            missing.append("PINECONE_API_KEY")
        if not settings.pinecone_index:
            missing.append("PINECONE_INDEX")
        # Generation now runs on the local LLM (Ollama) — no Anthropic key required.
        if missing:
            log.warning("RAG disabled — missing env vars: %s", ", ".join(missing))
            self._ready = False
            return False

        try:
            import voyageai
            self._voyage = voyageai.Client(api_key=settings.voyageai_api_key)
        except ImportError:
            log.warning("RAG disabled — install voyageai: pip install voyageai")
            self._ready = False
            return False

        try:
            from pinecone import Pinecone
            pc          = Pinecone(api_key=settings.pinecone_api_key)
            self._index = pc.Index(settings.pinecone_index)
        except ImportError:
            log.warning("RAG disabled — install pinecone: pip install pinecone")
            self._ready = False
            return False
        except Exception as exc:
            log.warning("RAG disabled — Pinecone init error: %s", exc)
            self._ready = False
            return False

        try:
            # _anthro is the text-generation client. It is now the local LLM
            # (Ollama) drop-in, which exposes the same messages.create() surface.
            from backend.ai.local_llm import get_local_llm_client
            self._anthro = get_local_llm_client(timeout=60.0)
        except Exception as exc:
            log.warning("RAG disabled — local LLM client init error: %s", exc)
            self._ready = False
            return False

        log.info("RAG service initialised (voyage=%s, pinecone index=%s)",
                 EMBED_MODEL, settings.pinecone_index)
        self._ready = True
        return True

    # ──────────────────────────────────────────────────────────────────
    # Embedding
    # ──────────────────────────────────────────────────────────────────

    def _embed(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        import time as _time
        all_emb: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i: i + EMBED_BATCH]
            # Retry up to 3 times with exponential backoff for rate limit errors
            for attempt in range(3):
                try:
                    result = self._voyage.embed(batch, model=EMBED_MODEL, input_type=input_type)
                    all_emb.extend(result.embeddings)
                    break
                except Exception as exc:
                    err = str(exc)
                    is_rate_limit = (
                        "rate" in err.lower()
                        or "429" in err
                        or "too many" in err.lower()
                        or "rpm" in err.lower()
                        or "payment" in err.lower()
                    )
                    if is_rate_limit and attempt < 2:
                        wait = 25 * (attempt + 1)
                        log.warning("Voyage AI rate limited — retrying in %ds (attempt %d/3)", wait, attempt + 1)
                        _time.sleep(wait)
                        continue
                    raise
        return all_emb

    # ──────────────────────────────────────────────────────────────────
    # Chunk building
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_chunks(
        session_id:     str,
        tenant_id:      str,
        user_id:        str,
        session_name:   str,
        result:         dict,
        inspection:     dict,
        forecasts:      dict,
    ) -> list[dict]:
        """
        Convert session data into natural-language text chunks.
        Returns list of {id, text, metadata} dicts.
        text is stored in metadata so it can be retrieved alongside the vector.

        Metadata schema (all fields indexed in Pinecone):
          session_id     — for row-level filter within a tenant namespace
          user_id        — for per-user access control
          tenant_id      — redundant with namespace but useful for audit queries
          project_id     — alias for session_id (matches spec terminology)
          document_title — human-readable session name
          chunk_type     — dataset_profile | model_summary | sku_metrics | …
          timestamp      — ISO-8601 UTC indexing time
          sku / model    — optional narrowing fields
        """
        chunks: list[dict] = []
        result     = result     or {}
        inspection = inspection or {}
        forecasts  = forecasts  or {}
        ts         = datetime.now(timezone.utc).isoformat()

        def _chunk(cid: str, text: str, extra_meta: dict) -> dict:
            meta = {
                "text":           text,
                "session_id":     session_id,
                "project_id":     session_id,   # spec alias
                "tenant_id":      tenant_id,
                "user_id":        user_id,
                "document_title": session_name,
                "timestamp":      ts,
            }
            meta.update(extra_meta)
            return {"id": f"{tenant_id}_{session_id}_{cid}", "text": text, "metadata": meta}

        # ── 1. Dataset profile ────────────────────────────────────────
        profile = inspection.get("profile", {})
        stats   = profile.get("stats", {})
        rec     = profile.get("recommended", {})
        warns   = "; ".join(profile.get("warnings", [])) or "none"
        chunks.append(_chunk("profile", (
            f"Dataset overview: {stats.get('n_rows', '?')} total rows, "
            f"{stats.get('n_skus', stats.get('n_groups', '?'))} unique SKUs, "
            f"data frequency: {rec.get('freq', 'unknown')}, "
            f"date range {stats.get('date_min', '?')} to {stats.get('date_max', '?')}. "
            f"Dataset warnings: {warns}."
        ), {"chunk_type": "dataset_profile"}))

        metrics = result.get("metrics", {})

        # ── 2. Per-model aggregate summary ───────────────────────────
        for model, m in (metrics.get("by_model") or {}).items():
            mae  = m.get("avg_mae")  or 0
            rmse = m.get("avg_rmse") or 0
            wape = m.get("avg_wape") or 0
            chunks.append(_chunk(f"model_{model}", (
                f"Overall performance of {model} across all SKUs: "
                f"average MAE={mae:.4f}, average RMSE={rmse:.4f}, average WAPE={wape:.4f}. "
                f"Lower values indicate better forecast accuracy."
            ), {"chunk_type": "model_summary", "model": model}))

        # ── 3. Per-SKU metrics ────────────────────────────────────────
        sku_map: dict[str, list] = {}
        for row in metrics.get("rows") or []:
            sku = str(row.get("sku") or "__all__")
            sku_map.setdefault(sku, []).append(row)

        for sku, rows in sku_map.items():
            parts = []
            for r in rows:
                mae  = r.get("mae")
                wape = r.get("wape")
                rmse = r.get("rmse")
                mae_s  = f"{mae:.4f}"  if mae  is not None else "N/A"
                m_str  = f"{r.get('model')}: MAE={mae_s}"
                if wape is not None:
                    m_str += f", WAPE={wape:.4f}"
                if rmse is not None:
                    m_str += f", RMSE={rmse:.4f}"
                parts.append(m_str)
            chunks.append(_chunk(f"sku_metrics_{sku}", (
                f"Forecast accuracy for SKU {sku}: " + "; ".join(parts) + "."
            ), {"chunk_type": "sku_metrics", "sku": sku}))

        # ── 4. Inventory recommendations ────────────────────────────
        for rec_item in (result.get("inventory", {}) or {}).get("recommendations") or []:
            sku    = str(rec_item.get("sku", ""))
            risk   = (rec_item.get("stockout_risk") or 0) * 100
            action = rec_item.get("action", "OK")
            chunks.append(_chunk(f"inventory_{sku}", (
                f"Inventory recommendation for SKU {sku}: "
                f"reorder point {rec_item.get('reorder_point', 0):.1f} units, "
                f"safety stock {rec_item.get('safety_stock', 0):.1f} units, "
                f"stockout risk {risk:.1f}%, "
                f"estimated holding cost {rec_item.get('holding_cost', 0):.2f}. "
                f"Recommended action: {action}."
            ), {"chunk_type": "inventory", "sku": sku}))

        # ── 5. Data quality ──────────────────────────────────────────
        for sku, report in (result.get("data_quality") or {}).items():
            if not isinstance(report, dict):
                continue
            miss  = (report.get("missing_pct") or 0) * 100
            w_str = "; ".join(report.get("warnings") or []) or "none"
            chunks.append(_chunk(f"dq_{sku}", (
                f"Data quality for SKU {sku}: "
                f"quality score {report.get('quality_score', 0):.2f}/1.0, "
                f"series type: {report.get('series_type', 'unknown')}, "
                f"{report.get('n_rows', 0)} rows, "
                f"{miss:.1f}% missing values, "
                f"{report.get('n_outliers', 0)} outliers detected. "
                f"Valid for training: {report.get('is_valid', False)}. "
                f"Warnings: {w_str}."
            ), {"chunk_type": "data_quality", "sku": sku}))

        # ── 6. Routing plan ──────────────────────────────────────────
        routing = result.get("routing") or {}
        if routing:
            lines = [
                f"SKU {sku} assigned to: {', '.join(sorted(models))}"
                for sku, models in routing.items()
            ]
            chunks.append(_chunk("routing", (
                "Model routing plan — which models were assigned to each SKU based on its "
                "series characteristics (stable, seasonal, volatile, intermittent, short): "
                + " | ".join(lines)
            ), {"chunk_type": "routing"}))

        # ── 7. Config summary ────────────────────────────────────────
        cfg = result.get("config") or {}
        if cfg:
            tr  = cfg.get("training", {}) or {}
            fc  = cfg.get("forecast",  {}) or {}
            ft  = cfg.get("features",  {}) or {}
            col = cfg.get("columns",   {}) or {}
            chunks.append(_chunk("config", (
                f"Session configuration: "
                f"forecasting target '{col.get('target', '?')}' column, "
                f"date column '{col.get('date', '?')}', "
                f"group/SKU column '{col.get('group', 'none')}'. "
                f"Training strategy: {tr.get('train_ratio', 0.8)*100:.0f}%/test split, "
                f"walk-forward validation with {tr.get('wfv_splits', 3)} splits, "
                f"minimum history {tr.get('min_history', 20)} rows per SKU. "
                f"Forecast horizon: {fc.get('horizon', 14)} steps ahead. "
                f"Feature engineering: lag features {ft.get('lags', [])}, "
                f"rolling windows {ft.get('rolling', [])}, "
                f"calendar features {'enabled' if ft.get('calendar') else 'disabled'}."
            ), {"chunk_type": "config"}))

        # ── 8. Forecast trend summaries ──────────────────────────────
        for sku, models_dict in forecasts.items():
            if not isinstance(models_dict, dict):
                continue
            for model_name, series in models_dict.items():
                fc_pts = (series.get("forecast") or []) if isinstance(series, dict) else []
                values = [
                    p["value"] for p in fc_pts
                    if isinstance(p, dict) and p.get("value") is not None
                ]
                if len(values) < 2:
                    continue
                avg_v  = sum(values) / len(values)
                trend  = (
                    "upward"   if values[-1] > values[0] * 1.05 else
                    "downward" if values[-1] < values[0] * 0.95 else
                    "stable"
                )
                pct_change = ((values[-1] - values[0]) / max(abs(values[0]), 1e-9)) * 100
                chunks.append(_chunk(f"forecast_{sku}_{model_name}", (
                    f"Forecast for SKU {sku} using {model_name}: "
                    f"{len(values)}-step horizon, "
                    f"starts at {values[0]:.2f} units, "
                    f"ends at {values[-1]:.2f} units "
                    f"({pct_change:+.1f}% change), "
                    f"average {avg_v:.2f} units per period. "
                    f"Overall trend: {trend}."
                ), {"chunk_type": "forecast_summary", "sku": sku, "model": model_name}))

        return chunks

    # ──────────────────────────────────────────────────────────────────
    # Indexing
    # ──────────────────────────────────────────────────────────────────

    def index_session(
        self,
        tenant_id:    str,
        session_id:   str,
        result:       dict,
        inspection:   dict,
        forecasts:    dict | None = None,
        user_id:      str = "system",
        session_name: str = "",
    ) -> int:
        """
        Build chunks, embed with Voyage AI, upsert to Pinecone namespace=tenant_id.
        Returns number of vectors upserted (0 on failure or disabled).
        """
        if not self._init():
            return 0

        try:
            chunks = self._build_chunks(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                session_name=session_name or session_id,
                result=result,
                inspection=inspection,
                forecasts=forecasts or {},
            )
            if not chunks:
                log.info("RAG: no chunks for session %s", session_id)
                return 0

            texts      = [c["text"] for c in chunks]
            embeddings = self._embed(texts, input_type="document")

            vectors = [
                {"id": c["id"], "values": emb, "metadata": c["metadata"]}
                for c, emb in zip(chunks, embeddings)
            ]

            # Namespace = tenant_id  → physical company isolation
            for i in range(0, len(vectors), UPSERT_BATCH):
                self._index.upsert(
                    vectors=vectors[i: i + UPSERT_BATCH],
                    namespace=tenant_id,
                )

            log.info("RAG: indexed %d vectors (session=%s tenant=%s user=%s)",
                     len(vectors), session_id, tenant_id, user_id)
            return len(vectors)

        except Exception as exc:
            log.error("RAG indexing failed (session=%s): %s", session_id, exc, exc_info=True)
            return 0

    # ──────────────────────────────────────────────────────────────────
    # Retrieval
    # ──────────────────────────────────────────────────────────────────

    def _retrieve_documents(
        self,
        tenant_id: str,
        query_vec: list[float],
        top_k:     int = 4,
    ) -> list[dict]:
        """
        Retrieve document chunks for a tenant (no session filter — documents are
        shared across all sessions within a tenant).
        Returns [{text, score, doc_id, document_title}] sorted by score descending.
        """
        try:
            resp = self._index.query(
                vector=query_vec,
                top_k=top_k,
                include_metadata=True,
                namespace=tenant_id,
                filter={"source_type": {"$eq": "document"}},
            )
            results = []
            for m in resp.get("matches", []):
                meta = m.get("metadata") or {}
                if "text" in meta:
                    results.append({
                        "text":           meta["text"],
                        "score":          float(m.get("score", 0.0)),
                        "doc_id":         meta.get("doc_id", ""),
                        "document_title": meta.get("document_title", ""),
                        "page":           meta.get("page", 1),
                        "source":         "document",
                    })
            return results
        except Exception as exc:
            log.warning("RAG document retrieval failed: %s", exc)
            return []

    def _retrieve(
        self,
        tenant_id:   str,
        session_id:  str,
        user_id:     str,
        query_vec:   list[float],
        top_k:       int = TOP_K_DEFAULT,
        chunk_types: list[str] | None = None,
    ) -> list[dict]:
        """
        Query Pinecone with dual security filter:
          namespace = tenant_id   (company isolation)
          filter    = session_id AND user_id  (row-level isolation)

        Optional chunk_types restricts retrieval to specific data source categories
        (dataset_profile, model_summary, sku_metrics, inventory, data_quality,
         routing, config, forecast_summary).

        Returns list of {text, score} dicts, sorted descending by score.
        Caller must apply MIN_SCORE threshold.
        """
        try:
            filter_clause: dict = {
                "$and": [
                    {"session_id": {"$eq": session_id}},
                    {"user_id":    {"$eq": user_id}},
                ]
            }
            if chunk_types:
                filter_clause["$and"].append({"chunk_type": {"$in": chunk_types}})

            resp = self._index.query(
                vector=query_vec,
                top_k=top_k,
                include_metadata=True,
                namespace=tenant_id,
                filter=filter_clause,
            )
            results = []
            for m in resp.get("matches", []):
                meta = m.get("metadata") or {}
                if "text" in meta:
                    results.append({
                        "text":  meta["text"],
                        "score": float(m.get("score", 0.0)),
                    })
            return results
        except Exception as exc:
            log.warning("RAG retrieval failed: %s", exc)
            return []

    # ──────────────────────────────────────────────────────────────────
    # Generation
    # ──────────────────────────────────────────────────────────────────

    def _generate(
        self,
        question:        str,
        retrieved_texts: list[str],
        sku:             str | None,
        history:         list[dict],
    ) -> str:
        context_block = "\n\n".join(
            f"[Retrieved context {i+1}]\n{text}"
            for i, text in enumerate(retrieved_texts)
        ) if retrieved_texts else "(No relevant context retrieved from vector store.)"

        sku_note     = f"\nFocus the answer on SKU: {sku}." if sku else ""
        user_content = (
            f"{context_block}\n\n"
            f"---\n"
            f"Question: {question}{sku_note}"
        )

        messages = [
            {"role": turn["role"], "content": turn["content"]}
            for turn in (history or [])[-MAX_HISTORY:]
        ]
        messages.append({"role": "user", "content": user_content})

        response = self._anthro.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text

    # ──────────────────────────────────────────────────────────────────
    # Public query API
    # ──────────────────────────────────────────────────────────────────

    def query(
        self,
        tenant_id:        str,
        session_id:       str,
        question:         str,
        user_id:          str = "unknown",
        sku:              str | None        = None,
        history:          list[dict] | None = None,
        top_k:            int = TOP_K_DEFAULT,
        chunk_types:      list[str] | None  = None,
        include_documents: bool = True,
    ) -> dict:
        """
        Full RAG pipeline: embed → retrieve (filtered by tenant+session+user) → generate.

        Security guarantees:
          1. Namespace = tenant_id  — no cross-company data ever mixed
          2. Metadata filter = session_id + user_id — row-level within company
          3. MIN_SCORE threshold — off-topic queries never reach the LLM

        Returns:
            {
                "answer":         str,
                "retrieved":      list[str],   # chunk texts used
                "source":         "rag" | "fallback" | "off_topic" | "no_access",
            }
        """
        if not self._init():
            return self._fallback(tenant_id, session_id, user_id, question, sku)

        # Bake SKU into the query text for better retrieval targeting
        query_text = f"[SKU: {sku}] {question}" if sku else question

        try:
            query_vec = self._embed([query_text], input_type="query")[0]
        except Exception as exc:
            log.warning("RAG embed failed: %s — falling back", exc)
            return self._fallback(tenant_id, session_id, user_id, question, sku)

        # ── Retrieve session chunks with tenant + user security filter ──
        scored = self._retrieve(
            tenant_id, session_id, user_id, query_vec,
            top_k=top_k, chunk_types=chunk_types,
        )

        # ── Optionally merge document chunks (tenant-scoped) ──────────
        if include_documents:
            doc_results = self._retrieve_documents(tenant_id, query_vec, top_k=4)
            # Tag source before merging so the generator can cite them
            for r in doc_results:
                r.setdefault("source", "document")
            # Interleave by score: merge and re-sort, keep top (top_k + 4)
            combined = scored + doc_results
            combined.sort(key=lambda x: x["score"], reverse=True)
            scored = combined[: top_k + 4]

        # ── No results → user has no indexed data in this session ─────
        if not scored:
            log.info("RAG: no vectors found for session=%s user=%s tenant=%s",
                     session_id, user_id, tenant_id)
            return {
                "answer":    _NO_ACCESS_REPLY,
                "retrieved": [],
                "source":    "no_access",
            }

        # ── Similarity threshold — reject off-topic queries ───────────
        max_score = max(r["score"] for r in scored)
        if max_score < MIN_SCORE:
            log.info("RAG: max similarity %.3f < MIN_SCORE %.3f — off-topic rejection",
                     max_score, MIN_SCORE)
            return {
                "answer":    _OFF_TOPIC_REPLY,
                "retrieved": [],
                "source":    "off_topic",
            }

        retrieved_texts = [r["text"] for r in scored]

        try:
            answer = self._generate(question, retrieved_texts, sku, history or [])
            return {"answer": answer, "retrieved": retrieved_texts, "source": "rag"}
        except Exception as exc:
            log.error("RAG generation failed: %s", exc, exc_info=True)
            # Retrieval succeeded but LLM is unavailable — try fallback,
            # then report "rag_retrieved" so callers know security checks passed.
            fb = self._fallback(tenant_id, session_id, user_id, question, sku)
            if fb.get("source") in ("fallback", "no_llm"):
                return fb
            # LLM completely unavailable — return retrieved context with degraded source
            ctx_dump = "\n\n".join(f"[Context {i+1}]\n{t}" for i, t in enumerate(retrieved_texts))
            return {
                "answer":    f"[LLM unavailable — raw retrieved context]\n\n{ctx_dump}",
                "retrieved": retrieved_texts,
                "source":    "rag_retrieved",
            }

    # ──────────────────────────────────────────────────────────────────
    # Fallback (no RAG — direct context dump to Claude)
    # ──────────────────────────────────────────────────────────────────

    def _fallback(
        self,
        tenant_id:  str,
        session_id: str,
        user_id:    str,
        question:   str,
        sku:        str | None,
    ) -> dict:
        """
        No vector retrieval available — build structured context from DB and
        call Claude directly (the original analyst approach, enhanced).
        """
        import json

        result: dict     = {}
        inspection: dict = {}
        try:
            from backend.db import session_store
            result     = session_store.get_training_result(tenant_id, session_id) or {}
            inspection = session_store.get_field(tenant_id, session_id, "inspection") or {}
        except Exception as db_exc:
            log.warning("_fallback: DB unavailable (%s) — continuing without context", db_exc)

        context: dict = {
            "metrics_summary": result.get("metrics", {}).get("by_model", {}),
            "inventory":       result.get("inventory", {}).get("recommendations", [])[:20],
            "data_quality":    result.get("data_quality", {}),
            "routing":         result.get("routing", {}),
        }
        if sku:
            rows = result.get("metrics", {}).get("rows", [])
            context["sku_metrics"] = [r for r in rows if str(r.get("sku")) == str(sku)]

        profile = inspection.get("profile", {})
        if profile:
            context["dataset"] = {
                "n_rows": profile.get("stats", {}).get("n_rows"),
                "n_skus": profile.get("stats", {}).get("n_skus"),
                "freq":   profile.get("recommended", {}).get("freq"),
            }

        sku_note = f" Focus on SKU: {sku}." if sku else ""
        user_msg = (
            f"Session context (JSON):\n```json\n"
            f"{json.dumps(context, indent=2, default=str)}\n```\n\n"
            f"Question: {question}{sku_note}"
        )

        try:
            resp = self._anthro.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            return {"answer": resp.content[0].text, "retrieved": [], "source": "fallback"}
        except Exception as exc:
            log.error("Fallback LLM call failed: %s", exc)
            return {
                "answer":    f"LLM call failed: {exc}. Context available: {list(context.keys())}",
                "retrieved": [],
                "source":    "error",
            }

    # ──────────────────────────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────────────────────────

    def delete_session(self, tenant_id: str, session_id: str) -> None:
        """Remove all vectors for a session from the tenant namespace."""
        if not self._init():
            return
        try:
            # list() yields ListResponse pages; each page.vectors is a list of ListItem(id=…)
            prefix  = f"{tenant_id}_{session_id}_"
            id_list: list[str] = []
            for page in self._index.list(prefix=prefix, namespace=tenant_id):
                for item in (page.vectors or []):
                    id_list.append(item.id)
            if id_list:
                self._index.delete(ids=id_list, namespace=tenant_id)
                log.info("RAG: deleted %d vectors for session=%s tenant=%s",
                         len(id_list), session_id, tenant_id)
        except Exception as exc:
            log.warning("RAG delete failed (session=%s): %s", session_id, exc)

    def is_indexed(self, tenant_id: str, session_id: str, user_id: str) -> dict:
        """
        Returns {"indexed": bool, "vector_count": int}.
        Checks within tenant namespace filtered by session_id.
        """
        if not self._init():
            return {"indexed": False, "vector_count": 0}
        try:
            # Use a zero-vector probe with metadata filter to count matching vectors
            zero_vec = [0.0] * EMBED_DIM
            resp = self._index.query(
                vector=zero_vec,
                top_k=1,
                include_metadata=False,
                namespace=tenant_id,
                filter={"session_id": {"$eq": session_id}},
            )
            count = len(resp.get("matches", []))
            return {"indexed": count > 0, "vector_count": count}
        except Exception as exc:
            log.warning("RAG is_indexed check failed: %s", exc)
            return {"indexed": False, "vector_count": 0}


# Module-level singleton
rag = RAGService()
