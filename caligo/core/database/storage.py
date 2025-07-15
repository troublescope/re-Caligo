# Taken from https://github.com/animeshxd/pyromongo

import asyncio
import base64
import inspect
import struct
import time
from typing import Any, List, Optional, Tuple, Union

from pymongo import UpdateOne
from pyrogram.raw.types.input_peer_channel import InputPeerChannel
from pyrogram.raw.types.input_peer_chat import InputPeerChat
from pyrogram.raw.types.input_peer_user import InputPeerUser
from pyrogram.storage.sqlite_storage import get_input_peer
from pyrogram.storage.storage import Storage

from . import AsyncDatabase


class PersistentStorage(Storage):
    """
    Persistent storage implementation using MongoDB.

    Parameters:
        name (``str``):
            The name of the session.
        database (``AsyncDatabase``):
            Required database object of AsyncDatabase.
        remove_peers (``bool``, *optional*):
            Remove peers collection on logout (by default, it will not remove peers).
            Defaults to False.
    """

    db: AsyncDatabase
    lock: asyncio.Lock
    USERNAME_TTL = 8 * 60 * 60

    def __init__(
        self, name: str, database: AsyncDatabase, remove_peers: bool = False
    ) -> None:
        # Propagate initialization with name parameter
        super().__init__(name)

        self.db = database
        self.lock = asyncio.Lock()

        # Use name parameter for collection names
        self._peer = database[f"{name}_PEERS"]
        self._usernames = database[f"{name}_USERNAMES"]
        self._remove_peers = remove_peers
        self._session = database[f"{name}_SESSION"]

    async def open(self) -> None:
        """Opens the storage engine."""
        """
        dc_id     INTEGER PRIMARY KEY,
        api_id    INTEGER,
        test_mode INTEGER,
        auth_key  BLOB,
        date      INTEGER NOT NULL,
        user_id   INTEGER,
        is_bot    INTEGER
        """

        if await self._session.find_one({"_id": 0}, {}):
            return

        await self._session.insert_one(
            {
                "_id": 0,
                "dc_id": 2,
                "api_id": None,
                "test_mode": None,
                "auth_key": b"",
                "date": 0,
                "user_id": 0,
                "is_bot": 0,
            }
        )

    async def save(self) -> None:
        """Saves the current state of the storage engine."""
        pass

    async def close(self) -> None:
        """Closes the storage engine."""
        pass

    async def delete(self) -> None:
        """Deletes the storage file."""
        try:
            await self._session.delete_one({"_id": 0})
            if self._remove_peers:
                await self._peer.delete_many({})
                await self._usernames.delete_many({})
        except Exception:  # skipcq: PYL-W0703
            return

    async def update_peers(self, peers: List[Tuple[int, int, str, str]]) -> None:
        """
        Update the peers table with the provided information.

        Parameters:
            peers (``List[Tuple[int, int, str, str]]``):
                A list of tuples containing the information of the peers to be updated.
                Each tuple must contain: (id, access_hash, type, phone_number)
        """
        s = int(time.time())
        bulk = [
            UpdateOne(
                {"_id": i[0]},
                {
                    "$set": {
                        "access_hash": i[1],
                        "type": i[2],
                        "phone_number": i[3],
                        "last_update_on": s,
                    }
                },
                upsert=True,
            )
            for i in peers
        ]
        if not bulk:
            return

        await self._peer.bulk_write(bulk)

    async def update_usernames(self, usernames: List[Tuple[int, List[str]]]) -> None:
        """
        Update the usernames table with the provided information.

        Parameters:
            usernames (``List[Tuple[int, List[str]]]``):
                A list of tuples containing the information of the usernames to be updated.
                Each tuple must contain: (peer_id, list_of_usernames)
        """
        s = int(time.time())
        bulk = []

        for peer_id, username_list in usernames:
            # Remove existing usernames for this peer
            await self._usernames.delete_many({"peer_id": peer_id})

            # Insert new usernames
            for username in username_list:
                bulk.append(
                    UpdateOne(
                        {"peer_id": peer_id, "username": username},
                        {
                            "$set": {
                                "peer_id": peer_id,
                                "username": username,
                                "last_update_on": s,
                            }
                        },
                        upsert=True,
                    )
                )

        if bulk:
            await self._usernames.bulk_write(bulk)

    async def update_state(
        self, update_state: Tuple[int, int, int, int, int] = object
    ) -> Optional[Tuple[int, int, int, int, int]]:
        """
        Get or set the update state of the current session.

        Parameters:
            update_state (``Tuple[int, int, int, int, int]``, *optional*):
                A tuple containing the update state to set.
                Tuple must contain: (id, pts, qts, date, seq)
        """
        if update_state == object:
            # Get current state
            data = await self._session.find_one({"_id": 0}, {"update_state": 1})
            if not data or "update_state" not in data:
                return None
            return tuple(data["update_state"])
        else:
            # Set new state
            await self._session.update_one(
                {"_id": 0}, {"$set": {"update_state": list(update_state)}}, upsert=True
            )
            return None

    async def get_peer_by_id(
        self, peer_id: int
    ) -> Union[InputPeerUser, InputPeerChat, InputPeerChannel]:
        """
        Retrieve a peer by its ID.

        Parameters:
            peer_id (``int``):
                The ID of the peer to retrieve.
        """
        # id, access_hash, type
        res = await self._peer.find_one(
            {"_id": peer_id}, {"_id": 1, "access_hash": 1, "type": 1}
        )
        if not res:
            raise KeyError(f"ID not found: {peer_id}")

        return get_input_peer(*res.values())

    async def get_peer_by_username(
        self, username: str
    ) -> Union[InputPeerUser, InputPeerChat, InputPeerChannel]:
        """
        Retrieve a peer by its username.

        Parameters:
            username (``str``):
                The username of the peer to retrieve.
        """
        # Find username in usernames collection
        username_res = await self._usernames.find_one(
            {"username": username}, {"peer_id": 1, "last_update_on": 1}
        )

        if not username_res:
            raise KeyError(f"Username not found: {username}")

        if abs(time.time() - username_res["last_update_on"]) > self.USERNAME_TTL:
            raise KeyError(f"Username expired: {username}")

        # Get peer info from peers collection
        peer_id = username_res["peer_id"]
        res = await self._peer.find_one(
            {"_id": peer_id}, {"_id": 1, "access_hash": 1, "type": 1}
        )

        if not res:
            raise KeyError(f"Peer not found for username: {username}")

        return get_input_peer(res["_id"], res["access_hash"], res["type"])

    async def get_peer_by_phone_number(
        self, phone_number: str
    ) -> Union[InputPeerUser, InputPeerChat, InputPeerChannel]:
        """
        Retrieve a peer by its phone number.

        Parameters:
            phone_number (``str``):
                The phone number of the peer to retrieve.
        """
        #  _id, access_hash, type,
        res = await self._peer.find_one(
            {"phone_number": phone_number}, {"_id": 1, "access_hash": 1, "type": 1}
        )

        if not res:
            raise KeyError(f"Phone number not found: {phone_number}")

        return get_input_peer(*res.values())

    async def _get(self) -> Optional[Any]:
        """Internal method to get session attributes."""
        attr = inspect.stack()[2].function
        data = await self._session.find_one({"_id": 0}, {attr: 1})
        if not data:
            return

        return data.get(attr)

    async def _set(self, value: Any) -> None:
        """Internal method to set session attributes."""
        attr = inspect.stack()[2].function
        await self._session.update_one({"_id": 0}, {"$set": {attr: value}}, upsert=True)

    async def _accessor(self, value: Any = object) -> Any:
        """Internal accessor method for session attributes."""
        return await self._get() if value == object else await self._set(value)

    async def dc_id(self, value: int = object) -> Optional[int]:
        """
        Get or set the DC ID of the current session.

        Parameters:
            value (``int``, *optional*):
                The DC ID to set.
        """
        return await self._accessor(value)

    async def api_id(self, value: int = object) -> Optional[int]:
        """
        Get or set the API ID of the current session.

        Parameters:
            value (``int``, *optional*):
                The API ID to set.
        """
        return await self._accessor(value)

    async def test_mode(self, value: bool = object) -> Optional[bool]:
        """
        Get or set the test mode of the current session.

        Parameters:
            value (``bool``, *optional*):
                The test mode to set.
        """
        return await self._accessor(value)

    async def auth_key(self, value: bytes = object) -> Optional[bytes]:
        """
        Get or set the authorization key of the current session.

        Parameters:
            value (``bytes``, *optional*):
                The authorization key to set.
        """
        return await self._accessor(value)

    async def date(self, value: int = object) -> Optional[int]:
        """
        Get or set the date of the current session.

        Parameters:
            value (``int``, *optional*):
                The date to set.
        """
        return await self._accessor(value)

    async def user_id(self, value: int = object) -> Optional[int]:
        """
        Get or set the user ID of the current session.

        Parameters:
            value (``int``, *optional*):
                The user ID to set.
        """
        return await self._accessor(value)

    async def is_bot(self, value: bool = object) -> Optional[bool]:
        """
        Get or set the bot flag of the current session.

        Parameters:
            value (``bool``, *optional*):
                The bot flag to set.
        """
        return await self._accessor(value)

    async def export_session_string(self) -> str:
        """
        Exports the session string for the current session.

        Returns:
            ``str``: The session string for the current session.
        """
        packed = struct.pack(
            self.SESSION_STRING_FORMAT,
            await self.dc_id(),
            await self.api_id(),
            await self.test_mode(),
            await self.auth_key(),
            await self.user_id(),
            await self.is_bot(),
        )

        return base64.urlsafe_b64encode(packed).decode().rstrip("=")
