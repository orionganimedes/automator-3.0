"""tests/test_memory_store.py - Pruebas del almacén de memoria SQLite (sin dependencias externas)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.memory.store import MemoryStore  # noqa: E402


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "test.db")
        self.store = MemoryStore(self.db)

    def test_llm_cache_hit_miss(self):
        self.assertIsNone(self.store.llm_get("m", "p1"))
        self.store.llm_set("m", "p1", "resp", ttl=3600)
        self.assertEqual(self.store.llm_get("m", "p1"), "resp")
        self.assertIsNone(self.store.llm_get("m", "p2"))

    def test_locator_stats(self):
        self.store.record_locator("template", "like", True)
        self.store.record_locator("template", "like", False)
        self.store.record_locator("vision", "like", True)
        stats = self.store.locator_stats()
        self.assertEqual(len(stats), 2)
        tpl = next(s for s in stats if s["strategy"] == "template")
        self.assertEqual(tpl["hits"], 1)
        self.assertEqual(tpl["misses"], 1)

    def test_reflections_roundtrip(self):
        rid = self.store.record_reflection(account="c1", archetype="Heredero", comment="hola")
        refs = self.store.recent_reflections(5)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["id"], rid)
        self.assertEqual(refs[0]["archetype"], "Heredero")

    def test_accounts_and_interactions(self):
        self.store.register_device("serial1", "Pixel")
        aid = self.store.register_account("serial1", "instagram", 0, "cuenta_1", "Cuenta 1")
        self.store.record_interaction(aid, "http://x", "like_and_comment", "comentario", "Buscador")
        self.assertEqual(len(self.store.get_accounts("serial1", "instagram")), 1)
        self.assertEqual(self.store.summary()["interactions"], 1)
        self.assertEqual(self.store.summary()["devices"], 1)

    def test_register_account_conflict_id(self):
        self.store.register_device("s1", "Dev")
        aid1 = self.store.register_account("s1", "fb", 0, "u1", "Label 1")
        self.assertGreater(aid1, 0)
        aid2 = self.store.register_account("s1", "fb", 1, "u1", "Label Updated")
        self.assertEqual(aid1, aid2)
        iid = self.store.record_interaction(aid2, "http://post", "like")
        self.assertGreater(iid, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
