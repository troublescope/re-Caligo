from __future__ import annotations

import asyncio
import base64
import inspect
import struct
import time
from typing import Any, NamedTuple, TypeAlias, Union

from pymongo import UpdateOne
from pymongo.asynchronous.database import AsyncDatabase
from pyrogram.raw.types.input_peer_channel import InputPeerChannel
from pyrogram.raw.types.input_peer_chat import InputPeerChat
from pyrogram.raw.types.input_peer_user import InputPeerUser
from pyrogram.storage.sqlite_storage import get_input_peer
from pyrogram.storage.storage import Storage

from caligo import util

PeerTuple: TypeAlias = tuple[int, int, str, str]
UsernameTuple: TypeAlias = tuple[int, list[str]]
UpdateStateTuple: TypeAlias = tuple[int, int, int, int, int]
InputPeer: TypeAlias = Union[InputPeerUser, InputPeerChat, InputPeerChannel]


class PeerInfo(NamedTuple):
    peer_id: int
    access_hash: int
    peer_type: str
    usernames: list[str]
    phone_number: str


_SENTINEL = object()


class PersistentStorage(Storage):
    USERNAME_TTL: int = 8 * 60 * 60

    def __init__(
        self, name: str, database: AsyncDatabase, remove_peers: bool = False
    ) -> None:
        super().__init__(name)
        self.db = database
        self.lock = asyncio.Lock()
        self._remove_peers = remove_peers
        self._peer = database[f"{name}_PEERS"]
        self._usernames = database[f"{name}_USERNAMES"]
        self._session = database[f"{name}_SESSION"]

    @staticmethod
    def _coerce_id(value: Any) -> Any:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    async def open(self) -> None:
        if await self._session.find_one({"_id": 0}, {}):
            return
        default_session = {
            "_id": 0,
            "dc_id": 2,
            "api_id": None,
            "test_mode": None,
            "auth_key": b"",
            "date": 0,
            "user_id": 0,
            "is_bot": 0,
        }
        await self._session.insert_one(default_session)

    async def save(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def delete(self) -> None:
        try:
            await self._session.delete_one({"_id": 0})
            if self._remove_peers:
                await asyncio.gather(
                    self._peer.delete_many({}),
                    self._usernames.delete_many({}),
                    return_exceptions=True,
                )
        except Exception:
            return

    def _validate_peer_tuple(self, peer_data: tuple) -> PeerInfo | None:
        try:
            match len(peer_data):
                case 3:
                    peer_id, access_hash, peer_type = peer_data
                    return PeerInfo(peer_id, access_hash, peer_type, [], "")
                case 4:
                    peer_id, access_hash, peer_type, fourth_element = peer_data
                    if isinstance(fourth_element, list):
                        return PeerInfo(
                            peer_id, access_hash, peer_type, fourth_element, ""
                        )
                    else:
                        return PeerInfo(
                            peer_id, access_hash, peer_type, [], fourth_element or ""
                        )
                case 5:
                    peer_id, access_hash, peer_type, usernames, phone_number = peer_data
                    usernames_list = usernames if isinstance(usernames, list) else []
                    return PeerInfo(
                        peer_id,
                        access_hash,
                        peer_type,
                        usernames_list,
                        phone_number or "",
                    )
                case n if n > 5:
                    peer_id, access_hash, peer_type, usernames, phone_number = (
                        peer_data[:5]
                    )
                    usernames_list = usernames if isinstance(usernames, list) else []
                    return PeerInfo(
                        peer_id,
                        access_hash,
                        peer_type,
                        usernames_list,
                        phone_number or "",
                    )
                case _:
                    return None
        except (ValueError, TypeError):
            return None

    async def update_peers(self, peers: list[tuple]) -> None:
        if not peers:
            return
        current_time = int(time.time())
        peer_bulk_ops = []
        username_bulk_ops = []
        peers_to_clear_usernames = set()
        for peer_data in peers:
            peer_info = await util.run_sync(self._validate_peer_tuple, peer_data)
            if peer_info is None:
                continue
            peer_id_coerced = self._coerce_id(peer_info.peer_id)
            peer_bulk_ops.append(
                UpdateOne(
                    {"_id": peer_id_coerced},
                    {
                        "$set": {
                            "access_hash": peer_info.access_hash,
                            "type": peer_info.peer_type,
                            "phone_number": peer_info.phone_number,
                            "last_update_on": current_time,
                        }
                    },
                    upsert=True,
                )
            )
            if peer_info.usernames:
                peers_to_clear_usernames.add(peer_id_coerced)
                for username in peer_info.usernames:
                    if username and username.strip():
                        username_bulk_ops.append(
                            UpdateOne(
                                {
                                    "peer_id": peer_id_coerced,
                                    "username": username.strip(),
                                },
                                {
                                    "$set": {
                                        "peer_id": peer_id_coerced,
                                        "username": username.strip(),
                                        "last_update_on": current_time,
                                    }
                                },
                                upsert=True,
                            )
                        )
        if peer_bulk_ops:
            await self._peer.bulk_write(peer_bulk_ops)
        if peers_to_clear_usernames:
            await self._usernames.delete_many(
                {"peer_id": {"$in": list(peers_to_clear_usernames)}}
            )
        if username_bulk_ops:
            await self._usernames.bulk_write(username_bulk_ops)

    async def update_usernames(self, usernames: list[UsernameTuple]) -> None:
        if not usernames:
            return
        current_time = int(time.time())
        bulk_ops = []
        peers_to_clear = set()
        for peer_id_raw, username_list in usernames:
            peer_id = self._coerce_id(peer_id_raw)
            peers_to_clear.add(peer_id)
            for username in username_list:
                if username and username.strip():
                    bulk_ops.append(
                        UpdateOne(
                            {"peer_id": peer_id, "username": username.strip()},
                            {
                                "$set": {
                                    "peer_id": peer_id,
                                    "username": username.strip(),
                                    "last_update_on": current_time,
                                }
                            },
                            upsert=True,
                        )
                    )
        if peers_to_clear:
            await self._usernames.delete_many(
                {"peer_id": {"$in": list(peers_to_clear)}}
            )
        if bulk_ops:
            await self._usernames.bulk_write(bulk_ops)

    async def update_state(
        self, update_state: UpdateStateTuple = _SENTINEL
    ) -> UpdateStateTuple | None:
        match update_state:
            case _ if update_state is _SENTINEL:
                if data := await self._session.find_one(
                    {"_id": 0}, {"update_state": 1}
                ):
                    if "update_state" in data:
                        return tuple(data["update_state"])
                return None
            case _:
                await self._session.update_one(
                    {"_id": 0},
                    {"$set": {"update_state": list(update_state)}},
                    upsert=True,
                )
                return None

    async def get_peer_by_id(self, peer_id: Any) -> InputPeer:
        peer_id_coerced = self._coerce_id(peer_id)
        res = await self._peer.find_one(
            {"_id": peer_id_coerced}, {"_id": 1, "access_hash": 1, "type": 1}
        )
        if res is None and isinstance(peer_id_coerced, int):
            res = await self._peer.find_one(
                {"_id": str(peer_id_coerced)}, {"_id": 1, "access_hash": 1, "type": 1}
            )
        if res is None and not isinstance(peer_id_coerced, int):
            try:
                alt = int(peer_id_coerced)
            except Exception:
                alt = None
            if alt is not None:
                res = await self._peer.find_one(
                    {"_id": alt}, {"_id": 1, "access_hash": 1, "type": 1}
                )
        match res:
            case None:
                raise KeyError(f"ID not found: {peer_id}")
            case _:
                return get_input_peer(res["_id"], res["access_hash"], res["type"])

    async def get_peer_by_username(self, username: str) -> InputPeer:
        normalized_username = username.lstrip("@").strip()
        username_res = await self._usernames.find_one(
            {"username": normalized_username}, {"peer_id": 1, "last_update_on": 1}
        )
        if username_res is None:
            raise KeyError(f"Username not found: {username}")
        current_time = time.time()
        age = abs(current_time - username_res["last_update_on"])
        if age > self.USERNAME_TTL:
            await self._usernames.delete_one({"username": normalized_username})
            raise KeyError(f"Username expired: {username}")
        peer_id = username_res["peer_id"]
        peer_id_coerced = self._coerce_id(peer_id)
        res = await self._peer.find_one(
            {"_id": peer_id_coerced}, {"_id": 1, "access_hash": 1, "type": 1}
        )
        if res is None and isinstance(peer_id_coerced, int):
            res = await self._peer.find_one(
                {"_id": str(peer_id_coerced)}, {"_id": 1, "access_hash": 1, "type": 1}
            )
        if res is None:
            await self._usernames.delete_many({"peer_id": peer_id})
            raise KeyError(f"Peer not found for username: {username}")
        return get_input_peer(res["_id"], res["access_hash"], res["type"])

    async def get_peer_by_phone_number(self, phone_number: str) -> InputPeer:
        normalized_phone = (
            phone_number.replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
        )
        res = await self._peer.find_one(
            {"phone_number": normalized_phone}, {"_id": 1, "access_hash": 1, "type": 1}
        )
        if res is None:
            raise KeyError(f"Phone number not found: {phone_number}")
        return get_input_peer(res["_id"], res["access_hash"], res["type"])

    async def _get(self) -> Any | None:
        attr = inspect.stack()[2].function
        match await self._session.find_one({"_id": 0}, {attr: 1}):
            case None:
                return None
            case data:
                return data.get(attr)

    async def _set(self, value: Any) -> None:
        attr = inspect.stack()[2].function
        await self._session.update_one({"_id": 0}, {"$set": {attr: value}}, upsert=True)

    async def _accessor(self, value: Any = _SENTINEL) -> Any:
        match value:
            case _ if value is _SENTINEL:
                return await self._get()
            case _:
                return await self._set(value)

    async def dc_id(self, value: int = _SENTINEL) -> int | None:
        return await self._accessor(value)

    async def api_id(self, value: int = _SENTINEL) -> int | None:
        return await self._accessor(value)

    async def test_mode(self, value: bool = _SENTINEL) -> bool | None:
        return await self._accessor(value)

    async def auth_key(self, value: bytes = _SENTINEL) -> bytes | None:
        return await self._accessor(value)

    async def date(self, value: int = _SENTINEL) -> int | None:
        return await self._accessor(value)

    async def user_id(self, value: int = _SENTINEL) -> int | None:
        return await self._accessor(value)

    async def is_bot(self, value: bool = _SENTINEL) -> bool | None:
        return await self._accessor(value)

    async def export_session_string(self) -> str:
        dc_id, api_id, test_mode, auth_key, user_id, is_bot = await asyncio.gather(
            self.dc_id(),
            self.api_id(),
            self.test_mode(),
            self.auth_key(),
            self.user_id(),
            self.is_bot(),
        )
        packed = struct.pack(
            self.SESSION_STRING_FORMAT,
            dc_id,
            api_id,
            test_mode,
            auth_key,
            user_id,
            is_bot,
        )
        return base64.urlsafe_b64encode(packed).decode().rstrip("=")

    async def list_peer_ids(self) -> list[int]:

        cursor = self._peer.find({}, {"_id": 1})
        return [doc["_id"] async for doc in cursor]

    async def list_usernames(self) -> list[str]:

        cursor = self._usernames.find({}, {"username": 1})
        return [doc["username"] async for doc in cursor]

    async def list_peers(self) -> list[tuple[int, int, str]]:

        cursor = self._peer.find({}, {"_id": 1, "access_hash": 1, "type": 1})
        return [
            (doc["_id"], doc.get("access_hash", 0), doc.get("type", ""))
            async for doc in cursor
        ]
