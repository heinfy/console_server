"""示例列表接口的真实 JWT、分页、筛选和参数校验回归。"""

import unittest
from urllib.parse import urlencode

from sqlalchemy.orm import Session

import test_auth_tokens as token_tests
from console_server.api.v1.endpoints import table_list
from console_server.model.auth_session import AuthSession
from console_server.model.rbac import User


class TableListEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = token_tests.TokenIdentityTests()
        self.fixture.password_hash = "unused-for-table-list"
        self.fixture.setUp()
        self.fixture.app.include_router(table_list.router, prefix="/api/v1")
        self.token = self.fixture.tokens()["access_token"]

    def tearDown(self):
        self.fixture.tearDown()

    async def request(self, **params):
        code, body, _ = await self.fixture.request(
            "GET", f"/api/v1/list/table-list?{urlencode(params)}", token=self.token,
        )
        return code, body

    async def test_pagination_and_refresh_return_stable_backend_data(self):
        code, first = await self.request()
        self.assertEqual(code, 200)
        self.assertEqual((first["total"], first["page"], first["page_size"], first["total_pages"]), (100, 1, 10, 10))
        self.assertEqual([item["key"] for item in first["items"]], list(range(10)))
        self.assertEqual(set(first["items"][0]), {"key", "name", "gender", "age", "createAt", "address", "tags"})
        self.assertEqual((await self.request())[1], first)
        _, last = await self.request(page=8, page_size=13)
        self.assertEqual([item["key"] for item in last["items"]], list(range(91, 100)))
        self.assertEqual((last["total"], last["total_pages"]), (100, 8))
        _, beyond = await self.request(page=9, page_size=13)
        self.assertEqual((beyond["items"], beyond["total"]), ([], 100))

    async def test_name_search_filters_before_pagination(self):
        code, result = await self.request(name=" TEST 1 ", page=2, page_size=5)
        self.assertEqual(code, 200)
        self.assertEqual((result["total"], result["total_pages"]), (12, 3))
        self.assertEqual([item["key"] for item in result["items"]], [13, 14, 15, 16, 17])
        _, empty = await self.request(name="没有匹配的数据")
        self.assertEqual((empty["items"], empty["total"], empty["total_pages"]), ([], 0, 0))
        self.assertEqual((await self.request(name="   "))[1]["total"], 100)

    async def test_combined_filters_and_inclusive_ranges(self):
        _, page = await self.request()
        item = page["items"][0]
        filters = dict(
            name="Test 1", gender=item["gender"], address=item["address"],
            age_min=item["age"], age_max=item["age"],
            created_at=item["createAt"], created_from=item["createAt"], created_to=item["createAt"],
        )
        code, result = await self.request(**filters)
        self.assertEqual((code, result["total"]), (200, 1))
        self.assertEqual(result["items"], [item])
        filters["gender"] = "男" if item["gender"] == "女" else "女"
        self.assertEqual((await self.request(**filters))[1]["total"], 0)
        _, youngest = await self.request(age_min=0, age_max=0)
        self.assertGreater(youngest["total"], 0)
        self.assertTrue(all(item["age"] == 0 for item in youngest["items"]))

    async def test_date_range_and_single_bound_filters(self):
        _, full = await self.request(page_size=100)
        first_date = min(item["createAt"] for item in full["items"])
        last_date = max(item["createAt"] for item in full["items"])
        self.assertEqual((await self.request(created_from=first_date, created_to=last_date))[1]["total"], 100)
        for params, boundary in [({"created_from": last_date}, last_date), ({"created_to": first_date}, first_date)]:
            code, result = await self.request(**params)
            self.assertEqual(code, 200)
            self.assertGreater(result["total"], 0)
            self.assertTrue(all(item["createAt"] == boundary for item in result["items"]))

    async def test_invalid_pagination_filters_and_ranges(self):
        for params in [
            {"page": 0}, {"page_size": 0}, {"page_size": 101},
            {"name": "a" * 129}, {"gender": "invalid"}, {"address": "invalid"},
            {"age_min": -1}, {"age_max": 101}, {"age_min": 80, "age_max": 20},
            {"created_at": "invalid"}, {"created_from": "2023-11-01", "created_to": "2023-10-01"},
        ]:
            with self.subTest(params=params):
                self.assertEqual((await self.request(**params))[0], 422)

    async def test_login_and_live_session_are_required(self):
        code, _, _ = await self.fixture.request("GET", "/api/v1/list/table-list")
        self.assertEqual(code, 401)
        refresh = self.fixture.tokens()["refresh_token"]
        code, _, _ = await self.fixture.request("GET", "/api/v1/list/table-list", token=refresh)
        self.assertEqual(code, 401)
        with Session(self.fixture.engine) as db:
            db.get(User, 1).is_active = False
            db.commit()
        self.assertEqual((await self.request())[0], 403)
        with Session(self.fixture.engine) as db:
            db.get(User, 1).is_active = True
            for session in db.query(AuthSession).all():
                session.revoked_at = session.expires_at
            db.commit()
        self.assertEqual((await self.request())[0], 401)


if __name__ == "__main__":
    unittest.main()
