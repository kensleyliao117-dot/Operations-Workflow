"""使用独立数据库检查采集快照的归属、去重和旧记录兼容。"""
import unittest
from dataclasses import replace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from collectors.base import CollectedCase
from database import Base
from models.rednote import RednoteCase, RednoteAnalysis
from models.user import Users
from schemas.rednote import RednoteCaseCreate
from services.exceptions import AuthenticationError, InvalidInputError
from services.rednote import create_case, save_collected_case, update_case


class RednotePersistenceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, info={"user_id": 1})
        self.addCleanup(self.db.close)
        self.db.add_all([Users(id=1, username="first"), Users(id=2, username="second")])
        self.db.commit()
        self.collected = CollectedCase(platform="rednote",
            source_url="https://www.xiaohongshu.com/explore/0123456789abcdef01234567",
            title="耳机案例", body="正文第一行\n正文第二行")

    def count(self):
        return self.db.scalar(select(func.count()).select_from(RednoteCase))

    def test_save_title_body_link_and_owner_without_creating_analysis(self):
        saved = save_collected_case(self.db, self.collected)
        saved_id = saved.id
        self.db.expire_all()
        stored = self.db.get(RednoteCase, saved_id)
        self.assertEqual((stored.name, stored.body, stored.link, stored.user_id),
                         (self.collected.title, self.collected.body, self.collected.source_url, 1))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RednoteAnalysis)), 0)

    def test_repeated_collection_reuses_case_and_preserves_saved_snapshot(self):
        first_id = save_collected_case(self.db, self.collected).id
        updated_source = CollectedCase(platform="rednote", source_url=self.collected.source_url,
                                        title="变更标题", body="变更正文")
        duplicate = save_collected_case(self.db, updated_source)
        self.assertEqual(duplicate.id, first_id)
        self.assertEqual((duplicate.name, duplicate.body), (self.collected.title, self.collected.body))
        self.assertEqual(self.count(), 1)

    def test_same_link_can_be_saved_by_different_users(self):
        first_id = save_collected_case(self.db, self.collected).id
        self.db.info["user_id"] = 2
        second = save_collected_case(self.db, self.collected)
        self.assertNotEqual(first_id, second.id)
        self.assertEqual(second.user_id, 2)
        self.assertEqual(self.count(), 2)

    def test_recollection_corrects_metrics_without_replacing_source_snapshot(self):
        first = save_collected_case(self.db, replace(self.collected,
            metrics={'likes': 3, 'favorites': 73, 'comments': 97}, metrics_raw={'likes': '3'}))
        corrected = save_collected_case(self.db, replace(self.collected,
            metrics={'likes': 133, 'favorites': 73, 'comments': 97}, metrics_raw={'likes': '133'}))
        self.assertEqual(corrected.id, first.id)
        self.assertEqual(corrected.metrics['likes'], 133)
        self.assertEqual(corrected.metrics_raw['likes'], '133')
        self.assertEqual(corrected.body, self.collected.body)
        self.assertEqual(self.count(), 1)

    def test_legacy_case_missing_body_is_backfilled_without_changing_name(self):
        old = create_case(self.db, RednoteCaseCreate(name="原来的名称", link=self.collected.source_url))
        old_id = old.id
        self.assertIsNone(old.body)
        saved = save_collected_case(self.db, self.collected)
        self.assertEqual(saved.id, old_id)
        self.assertEqual(saved.body, self.collected.body)
        self.assertEqual(saved.name, "原来的名称")

    def test_case_update_preserves_omitted_body_and_allows_explicit_clear(self):
        saved_id = save_collected_case(self.db, self.collected).id
        updated = update_case(self.db, saved_id, RednoteCaseCreate(name="修改名称", link=self.collected.source_url))
        self.assertEqual(updated.body, self.collected.body)
        cleared = update_case(self.db, saved_id, RednoteCaseCreate(name="修改名称", link=self.collected.source_url, body=None))
        self.assertIsNone(cleared.body)

    def test_missing_identity_or_invalid_content_is_not_saved(self):
        self.db.info.clear()
        with self.assertRaises(AuthenticationError):
            save_collected_case(self.db, self.collected)
        self.db.info["user_id"] = 1
        for collected in (CollectedCase("instagram", self.collected.source_url, "title", "body"),
                          CollectedCase("rednote", self.collected.source_url, " ", " ")):
            with self.assertRaises(InvalidInputError):
                save_collected_case(self.db, collected)
        self.assertEqual(self.count(), 0)


if __name__ == "__main__":
    unittest.main()
