import httpx
from .config import settings

BASE = "https://bankaccountdata.gocardless.com/api/v2"

class GoCardlessClient:
    def __init__(self):
        self.access_token = None

    async def new_token(self):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{BASE}/token/new/", json={"secret_id": settings.gc_secret_id, "secret_key": settings.gc_secret_key})
            r.raise_for_status()
            data = r.json()
            self.access_token = data["access"]
            return data

    async def request(self, method, path, **kwargs):
        if not self.access_token:
            await self.new_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.request(method, f"{BASE}{path}", headers=headers, **kwargs)
            if r.status_code == 401:
                await self.new_token()
                headers["Authorization"] = f"Bearer {self.access_token}"
                r = await client.request(method, f"{BASE}{path}", headers=headers, **kwargs)
            r.raise_for_status()
            return r.json()

    async def institutions(self, country="es"):
        return await self.request("GET", "/institutions/", params={"country": country})

    async def create_agreement(self, institution_id, max_historical_days=730, access_valid_for_days=90):
        return await self.request("POST", "/agreements/enduser/", json={
            "institution_id": institution_id,
            "max_historical_days": max_historical_days,
            "access_valid_for_days": access_valid_for_days,
            "access_scope": ["balances", "details", "transactions"],
        })

    async def create_requisition(self, institution_id, redirect_url, reference, agreement=None, user_language="es"):
        body = {"redirect": redirect_url, "institution_id": institution_id, "reference": reference, "user_language": user_language}
        if agreement:
            body["agreement"] = agreement
        return await self.request("POST", "/requisitions/", json=body)

    async def requisition(self, requisition_id):
        return await self.request("GET", f"/requisitions/{requisition_id}/")

    async def account_details(self, account_id):
        return await self.request("GET", f"/accounts/{account_id}/details/")

    async def balances(self, account_id):
        return await self.request("GET", f"/accounts/{account_id}/balances/")

    async def transactions(self, account_id):
        return await self.request("GET", f"/accounts/{account_id}/transactions/")
