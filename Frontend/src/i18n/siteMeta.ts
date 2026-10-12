// Search titles and descriptions of the public pages whose copy is not in a
// page catalogue of its own (the home page, the four subpages and the API
// reference). Both languages behind one interface, like LANDING: a page with
// no English title does not build. Titles stay under ~65 characters, the
// width a results page shows.
import type { Lang } from '@/i18n/translations'
import type { SubpageKey } from '@/components/landing/subpagePaths'

export interface PageMeta { title: string; description: string }

export interface SiteMetaCopy {
  home: PageMeta & { keywords: string[]; imageAlt: string }
  subpages: Record<SubpageKey, PageMeta>
  developers: PageMeta
}

const es: SiteMetaCopy = {
  home: {
    title: 'StockAI: predicción de ventas con IA, producto por producto',
    description:
      'StockAI (Stock AI) pronostica la venta de cada producto: modelos de IA compiten sobre tu ' +
      'historial y se prueban contra tu pasado. Con ese pronóstico, sabes qué pedir.',
    keywords: [
      'predicción de ventas', 'pronóstico de ventas', 'pronóstico de demanda',
      'forecasting', 'demanda comprometida', 'escenarios de demanda', 'S&OP',
      'distribuidores', 'mayoristas', 'compras', 'quiebre de stock', 'Costa Rica', 'Latinoamérica',
    ],
    imageAlt: 'StockAI: predicción de ventas con IA, producto por producto',
  },
  subpages: {
    pricing: {
      title: 'StockAI precio: código fuente por $14.999 USD, pago único',
      description:
        'StockAI se vende solo como código fuente: $14.999 USD en un pago único, para instalarlo en tu propia ' +
        'infraestructura. Incluye todas las funciones del producto, con API, servidor MCP y bot de WhatsApp.',
    },
    how: {
      title: 'StockAI: cómo funciona, de tus ventas al pronóstico y a la compra',
      description:
        'Sube tus ventas en CSV o Excel: los modelos compiten por pronosticar cada producto y, con ese ' +
        'pronóstico, StockAI te dice qué pedir. Los pasos, la regla del semáforo y cada pantalla con capturas reales.',
    },
    faq: {
      title: 'StockAI preguntas frecuentes: datos, pronóstico y compra',
      description:
        'Qué datos necesitas, cómo se mide el pronóstico, cómo se compra StockAI, si sirve con varias bodegas, ' +
        'cómo aprende el plazo de cada proveedor y quién ve tus datos en StockAI.',
    },
    security: {
      title: 'StockAI seguridad: tus datos, permisos y borrado completo',
      description:
        'Cada empresa ve solo sus datos, permisos por rol, credenciales cifradas y borrado completo si te vas. ' +
        'Así cuida StockAI tus ventas y tu inventario.',
    },
  },
  developers: {
    title: 'StockAI API para desarrolladores: documentación y ejemplos',
    description:
      'Conecta tu ERP, POS o sistema propio a StockAI: API key de lectura o escritura, límites de uso ' +
      'y la referencia completa de endpoints con ejemplos curl.',
  },
}

const en: SiteMetaCopy = {
  home: {
    title: 'StockAI: AI sales forecasting, product by product',
    description:
      'StockAI (Stock AI) forecasts the sales of every product: AI models compete on your ' +
      'history and are tested against your past. With that forecast, you know what to order.',
    keywords: [
      'sales forecasting', 'sales prediction', 'demand forecasting',
      'forecasting', 'committed demand', 'demand scenarios', 'S&OP',
      'distributors', 'wholesalers', 'purchasing', 'stockout', 'Costa Rica', 'Latin America',
    ],
    imageAlt: 'StockAI: AI sales forecasting, product by product',
  },
  subpages: {
    pricing: {
      title: 'StockAI pricing: source code for $14,999 USD, one-time payment',
      description:
        'StockAI is sold only as source code: $14,999 USD in a single payment, to install on your own ' +
        'infrastructure. It includes every feature of the product, with the API, MCP server and WhatsApp bot.',
    },
    how: {
      title: 'How StockAI works: from your sales to the forecast and the order',
      description:
        'Upload your sales as CSV or Excel: the models compete to forecast every product and, with that ' +
        'forecast, StockAI tells you what to order. The steps, the stock signal rule and every screen with real screenshots.',
    },
    faq: {
      title: 'StockAI FAQ: your data, the forecast and buying StockAI',
      description:
        'What data you need, how the forecast is measured, how StockAI is bought, whether it works with several warehouses, ' +
        'how it learns each supplier’s lead time and who sees your data in StockAI.',
    },
    security: {
      title: 'StockAI security: your data, permissions and full deletion',
      description:
        'Each company sees only its own data, role-based permissions, encrypted credentials and full deletion ' +
        'if you leave. This is how StockAI looks after your sales and your inventory.',
    },
  },
  developers: {
    title: 'StockAI API for developers: documentation and examples',
    description:
      'Connect your ERP, POS or in-house system to StockAI: read or write API keys, usage limits ' +
      'and the full endpoint reference with curl examples.',
  },
}

export const SITE_META: Record<Lang, SiteMetaCopy> = { es, en }
