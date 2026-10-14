"""Overlapping first clients must not expose Chroma's partially started system."""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from threading import Event

import pytest
from chromadb.config import System

from paper_agent.rag.vectorstore import VectorStore


def test_overlapping_cold_start_clients_both_become_usable(tmp_path, monkeypatch):
    entered = Event()
    release = Event()
    attempted = Event()
    original_start = System.start

    def delayed_start(system):
        entered.set()
        assert release.wait(10), "Cold-start test gate expired"
        return original_start(system)

    def second_client():
        attempted.set()
        return VectorStore(tmp_path / "db")

    monkeypatch.setattr(System, "start", delayed_start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(VectorStore, tmp_path / "db")
        try:
            assert entered.wait(5)
            second = pool.submit(second_client)
            assert attempted.wait(5)
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.2)
        finally:
            release.set()
        clients = [first.result(timeout=15), second.result(timeout=15)]
        try:
            assert [store.count() for store in clients] == [0, 0]
            assert [store.all_hits() for store in clients] == [[], []]
        finally:
            for store in clients:
                store.client.close()
