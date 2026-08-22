#!/usr/bin/env python3
"""
Gun Art Online mining cron task.

Designed for GitHub Actions or any scheduled runner:
- Run once and exit.
- Check mining status.
- Collect only when the current session is ready.
- Eat food only when HP/MP is below the configured threshold.
- Start a new mining session if no session is active after collection.
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any


BASE_URL = "https://gunart-backend.onrender.com"
DEFAULT_MINE_ZONE = "iron_mine"
MIN_COLLECT_SECONDS = 15 * 60


class ApiError(RuntimeError):
    pass


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def ratio(value: Any, maximum: Any) -> float:
    try:
        value_f = float(value)
        max_f = float(maximum)
    except (TypeError, ValueError):
        return 0.0
    if max_f <= 0:
        return 0.0
    return value_f / max_f


class GunArtClient:
    def __init__(
        self,
        username: str | None,
        password: str | None,
        token: str | None,
        base_url: str = BASE_URL,
    ) -> None:
        self.username = username
        self.password = password
        self.token = token
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        auth: bool = True,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        data = None
        if auth:
            if not self.token:
                self.login()
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                raw = res.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
                message = payload.get("error") or payload.get("message") or raw
            except json.JSONDecodeError:
                message = raw or f"HTTP {exc.code}"
            if exc.code == 401 and auth:
                self.token = None
                if retry_auth and self.username and self.password:
                    log("token expired, logging in with username/password")
                    self.login()
                    return self.request(method, path, body, auth, retry_auth=False)
            raise ApiError(f"{method} {path} failed: {message}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"{method} {path} failed: {exc.reason}") from exc

    def login(self) -> None:
        if not self.username or not self.password:
            raise ApiError("GAO_TOKEN is missing/expired and GAO_USERNAME/GAO_PASSWORD are not set")
        payload = self.request(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": self.password},
            auth=False,
        )
        token = payload.get("token")
        if not token:
            raise ApiError("login succeeded but no token was returned")
        self.token = token
        character = payload.get("character") or {}
        log(f"login ok: {character.get('name', self.username)}")

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/api/auth/me")

    def inventory(self) -> dict[str, Any]:
        return self.request("GET", "/api/inventory")

    def use_inventory_item(self, item_id: int) -> dict[str, Any]:
        return self.request("POST", f"/api/inventory/use/{item_id}")

    def mine_status(self) -> dict[str, Any]:
        return self.request("GET", "/api/town/mine/status")

    def mine_collect(self) -> dict[str, Any]:
        return self.request("POST", "/api/town/mine/collect")

    def mine_start(self, zone: str) -> dict[str, Any]:
        return self.request("POST", "/api/town/mine/start", {"zone": zone})


def summarize_result(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("message", "zone", "elapsedSeconds", "hp", "mp", "gold", "exp"):
        if result.get(key) is not None:
            parts.append(f"{key}={result[key]}")
    drops = result.get("drops") or result.get("items") or result.get("rewards")
    if drops:
        parts.append(f"drops={drops}")
    return "; ".join(parts) or "ok"


def character_from(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("character") or payload.get("player") or {}
    return value if isinstance(value, dict) else {}


def food_effect(food: dict[str, Any]) -> tuple[float, float]:
    effect = food.get("effect") or {}
    return float(effect.get("hp") or 0), float(effect.get("mp") or 0)


def eat_until_safe(
    client: GunArtClient,
    character: dict[str, Any],
    threshold: float,
    preferred_food_name: str | None,
    max_uses: int,
) -> dict[str, Any]:
    hp_pct = ratio(character.get("hp"), character.get("maxHp"))
    mp_pct = ratio(character.get("mp"), character.get("maxMp"))
    if hp_pct >= threshold and mp_pct >= threshold:
        return character

    inventory = client.inventory().get("items") or []
    foods = [
        dict(item)
        for item in inventory
        if item.get("type") == "consumable"
        and "Food" in (item.get("tags") or [])
        and int(item.get("quantity") or 0) > 0
        and sum(food_effect(item)) > 0
    ]
    if preferred_food_name:
        foods = [item for item in foods if str(item.get("name") or "") == preferred_food_name]
    if not foods:
        log("food needed, but no matching food found")
        return character

    for use_index in range(max_uses):
        current_hp = float(character.get("hp") or 0)
        current_mp = float(character.get("mp") or 0)
        max_hp = float(character.get("maxHp") or 0)
        max_mp = float(character.get("maxMp") or 0)
        hp_need = max(0.0, threshold * max_hp - current_hp)
        mp_need = max(0.0, threshold * max_mp - current_mp)
        if hp_need <= 0 and mp_need <= 0:
            break

        candidates = []
        for food in foods:
            if int(food.get("quantity") or 0) <= 0:
                continue
            restore_hp, restore_mp = food_effect(food)
            useful = min(restore_hp, hp_need) + min(restore_mp, mp_need)
            if useful <= 0:
                continue
            waste = max(0.0, restore_hp - hp_need) + max(0.0, restore_mp - mp_need)
            candidates.append((waste, -useful, -(restore_hp + restore_mp), food))
        if not candidates:
            break

        food = min(candidates, key=lambda entry: entry[:3])[3]
        result = client.use_inventory_item(int(food["item_id"]))
        log(f"eat {food.get('name') or food.get('item_id')} ({use_index + 1}/{max_uses}): {summarize_result(result)}")

        returned_character = character_from(result)
        if returned_character:
            character = {**character, **returned_character}
        else:
            restore_hp, restore_mp = food_effect(food)
            character = {
                **character,
                "hp": min(max_hp, current_hp + restore_hp),
                "mp": min(max_mp, current_mp + restore_mp),
            }
        food["quantity"] = int(food.get("quantity") or 0) - 1

    log(
        "after food: "
        f"HP={character.get('hp')}/{character.get('maxHp')} ({ratio(character.get('hp'), character.get('maxHp')):.0%}), "
        f"MP={character.get('mp')}/{character.get('maxMp')} ({ratio(character.get('mp'), character.get('maxMp')):.0%})"
    )
    return character


def run_once() -> int:
    token = os.getenv("GAO_TOKEN")
    username = os.getenv("GAO_USERNAME")
    password = os.getenv("GAO_PASSWORD")
    mine_zone = os.getenv("GAO_MINE_ZONE") or DEFAULT_MINE_ZONE
    food_name = os.getenv("GAO_FOOD_NAME") or "牛肉"
    threshold = float(os.getenv("GAO_SAFE_THRESHOLD") or "0.70")
    max_food_uses = int(os.getenv("GAO_MAX_FOOD_USES") or "10")

    if not token and (not username or not password):
        print("Set GAO_TOKEN or GAO_USERNAME/GAO_PASSWORD in GitHub Secrets.", file=sys.stderr)
        return 2

    client = GunArtClient(username=username, password=password, token=token)
    status = client.mine_status()
    active = bool(status.get("active"))
    elapsed = int(status.get("elapsedSeconds") or 0)
    log(f"mine status: active={active}, zone={status.get('zone')}, elapsed={elapsed}s")

    character: dict[str, Any] = character_from(status)
    if active:
        if elapsed < MIN_COLLECT_SECONDS:
            log("mine is still below 15 minutes; exiting")
            return 0
        collected = client.mine_collect()
        log(f"collect mine: {summarize_result(collected)}")
        character = character_from(collected) or character

    if not character:
        character = character_from(client.me())

    character = eat_until_safe(
        client=client,
        character=character,
        threshold=threshold,
        preferred_food_name=food_name,
        max_uses=max_food_uses,
    )

    started = client.mine_start(mine_zone)
    log(f"start mine {mine_zone}: {summarize_result(started)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
