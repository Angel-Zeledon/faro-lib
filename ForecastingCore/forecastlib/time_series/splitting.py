"""
Time-series train/test splitting strategies.

    from forecastlib.time_series.splitting import TimeSeriesSplitter

    splitter = TimeSeriesSplitter(n_splits=5, test_size=30)
    for train, test in splitter.split(dataset):
        model.fit(train)
        evaluate(test)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from forecastlib.data.dataset import Dataset


@dataclass
class SplitInfo:
    fold:       int
    train_size: int
    test_size:  int
    train_start: Optional[str]
    train_end:   Optional[str]
    test_start:  Optional[str]
    test_end:    Optional[str]


class TimeSeriesSplitter:
    """
    Expanding-window or rolling-window walk-forward cross-validation.

    Parameters
    ----------
    n_splits : int, default 5
        Number of train/test folds.
    test_size : int, optional
        Fixed number of rows (or time steps) in each test fold.
        If None, uses 1/(n_splits+1) of the data.
    gap : int, default 0
        Number of rows to drop between train and test (simulates deployment lag).
    expanding : bool, default True
        If True → expanding window (training grows each fold).
        If False → rolling window (training size is fixed).
    train_size : int, optional
        Fixed training window size (only used when expanding=False).

    Examples
    --------
    >>> splitter = TimeSeriesSplitter(n_splits=5, test_size=30)
    >>> for train_ds, test_ds in splitter.split(dataset):
    ...     print(train_ds.shape, test_ds.shape)
    """

    def __init__(
        self,
        n_splits: int = 5,
        test_size: Optional[int] = None,
        gap: int = 0,
        expanding: bool = True,
        train_size: Optional[int] = None,
    ):
        self.n_splits   = n_splits
        self.test_size  = test_size
        self.gap        = gap
        self.expanding  = expanding
        self.train_size = train_size

    # ------------------------------------------------------------------
    # Index-based split (works on any ordered DataFrame)
    # ------------------------------------------------------------------

    def split_indices(self, n: int) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Return list of (train_indices, test_indices) tuples."""
        test_sz = self.test_size or max(1, n // (self.n_splits + 1))
        min_train = max(2, n - self.n_splits * (test_sz + self.gap))

        splits = []
        for i in range(self.n_splits):
            test_end   = n - i * (test_sz + self.gap)
            test_start = test_end - test_sz
            train_end  = test_start - self.gap

            if train_end < 2 or test_start < 0:
                break

            if self.expanding:
                train_start = 0
            else:
                train_start = max(0, train_end - (self.train_size or train_end))

            train_idx = np.arange(train_start, train_end)
            test_idx  = np.arange(test_start, test_end)
            splits.append((train_idx, test_idx))

        # Return in chronological order (earliest fold first)
        return list(reversed(splits))

    # ------------------------------------------------------------------
    # Dataset-aware split
    # ------------------------------------------------------------------

    def split(
        self,
        dataset: "Dataset",
    ) -> Iterator[Tuple["Dataset", "Dataset"]]:
        """
        Yield (train_Dataset, test_Dataset) tuples.

        Parameters
        ----------
        dataset : Dataset

        Yields
        ------
        (train, test) : tuple of Dataset
        """
        df = dataset._df.reset_index(drop=True)
        splits = self.split_indices(len(df))

        for train_idx, test_idx in splits:
            train_df = df.iloc[train_idx].reset_index(drop=True)
            test_df  = df.iloc[test_idx].reset_index(drop=True)
            yield (
                type(dataset)(train_df, schema=dataset._schema.copy(), source=dataset._source),
                type(dataset)(test_df,  schema=dataset._schema.copy(), source=dataset._source),
            )

    # ------------------------------------------------------------------
    # Single split
    # ------------------------------------------------------------------

    def train_test_split(
        self,
        dataset: "Dataset",
        test_ratio: float = 0.2,
    ) -> Tuple["Dataset", "Dataset"]:
        """
        Simple chronological train/test split (no cross-validation).

        Parameters
        ----------
        test_ratio : float, default 0.2

        Returns
        -------
        (train, test) : tuple of Dataset
        """
        df  = dataset._df.reset_index(drop=True)
        cut = int(len(df) * (1 - test_ratio))
        cut = max(1, min(cut, len(df) - 1))

        train_df = df.iloc[:cut].reset_index(drop=True)
        test_df  = df.iloc[cut:].reset_index(drop=True)
        DS = type(dataset)
        return (
            DS(train_df, schema=dataset._schema.copy(), source=dataset._source),
            DS(test_df,  schema=dataset._schema.copy(), source=dataset._source),
        )

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def get_split_info(self, n: int) -> List[SplitInfo]:
        """Return metadata about each fold without materializing data."""
        info = []
        for i, (tr, te) in enumerate(self.split_indices(n)):
            info.append(SplitInfo(
                fold=i,
                train_size=len(tr),
                test_size=len(te),
                train_start=str(tr[0]) if len(tr) else None,
                train_end=str(tr[-1]) if len(tr) else None,
                test_start=str(te[0]) if len(te) else None,
                test_end=str(te[-1]) if len(te) else None,
            ))
        return info
