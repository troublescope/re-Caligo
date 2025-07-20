# Optimized for Python 3.12
from __future__ import annotations

import asyncio
import base64
import inspect
import struct
import time
from typing import Any, TypeAlias, Union

from pymongo import UpdateOne
from pyrogram.raw.types.input_peer_channel import InputPeerChannel
from pyrogram.raw.types.input_peer_chat import InputPeerChat
from pyrogram.raw.types.input_peer_user import InputPeerUser
from pyrogram.storage.sqlite_storage import get_input_peer
from pyrogram.storage.storage import Storage

from . import AsyncDatabase

# Type aliases
PeerTuple: TypeAlias = tuple[int, int, str, str]
UsernameTuple: TypeAlias = tuple[int, list[str]]
UpdateStateTuple: TypeAlias = tuple[int, int, int, int, int]
InputPeer: TypeAlias = Union[InputPeerUser, InputPeerChat, InputPeerChannel]

# Sentinel object parameter default.
_SENTINEL = object()


class PersistentStorage(Storage):
    """
    Persistent storage implementation using MongoDB.

    Parameters:
        name: The name of the session.
        database: Required database object of AsyncDatabase.
        remove_peers: Remove peers collection on logout (by default, it will not remove peers).
            Defaults to False.
    """

    USERNAME_TTL: int = 8 * 60 * 60

    def __init__(
        self, name: str, database: AsyncDatabase, remove_peers: bool = False
    ) -> None:
        super().__init__(name)

        self.db = database
        self.lock = asyncio.Lock()
        self._remove_peers = remove_peers

        # Use name parameter for collection names with f-strings
        self._peer = database[f"{name}_PEERS"]
        self._usernames = database[f"{name}_USERNAMES"]
        self._session = database[f"{name}_SESSION"]

    async def open(self) -> None:
        """Opens the storage engine."""
        if await self._session.find_one({"_id": 0}, {}):
            return

        # Use dict literal for better performance
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
        """Saves the current state of the storage engine."""

    async def close(self) -> None:
        """Closes the storage engine."""

    async def delete(self) -> None:
        """Deletes the storage file."""
        try:
            await self._session.delete_one({"_id": 0})
            if self._remove_peers:
                # Use asyncio.gather for concurrent operations
                await asyncio.gather(
                    self._peer.delete_many({}),
                    self._usernames.delete_many({}),
                    return_exceptions=True,
                )
        except Exception:
            return

    def _validate_peer_tuple(self, peer_data: tuple) -> PeerTuple | None:
        """
        Validate and normalize peer tuple data using pattern matching.

        Parameters:
            peer_data: Raw peer tuple data

        Returns:
            Normalized peer tuple or None if invalid
        """
        try:
            match len(peer_data):
                case 3:
                    # Handle case with 3 values: (id, access_hash, type)
                    peer_id, access_hash, peer_type = peer_data
                    return (peer_id, access_hash, peer_type, "")

                case 4:
                    # Handle case with 4 values: (id, access_hash, type, phone_number)
                    peer_id, access_hash, peer_type, phone_number = peer_data
                    return (peer_id, access_hash, peer_type, phone_number or "")

                case n if n > 4:
                    # Handle case with more than 4 values, take first 4
                    peer_id, access_hash, peer_type, phone_number = peer_data[:4]
                    return (peer_id, access_hash, peer_type, phone_number or "")

                case _:
                    # Invalid tuple length (< 3)
                    return None

        except (ValueError, TypeError):
            return None

    async def update_peers(self, peers: list[tuple]) -> None:
        """
        Update the peers table with the provided information.

        Parameters:
            peers: A list of tuples containing the information of the peers to be updated.
                Each tuple should contain: (id, access_hash, type) or (id, access_hash, type, phone_number)
        """
        if not peers:
            return

        current_time = int(time.time())
        bulk_ops = []

        for peer_data in peers:
            match self._validate_peer_tuple(peer_data):
                case (peer_id, access_hash, peer_type, phone_number):
                    bulk_ops.append(
                        UpdateOne(
                            {"_id": peer_id},
                            {
                                "$set": {
                                    "access_hash": access_hash,
                                    "type": peer_type,
                                    "phone_number": phone_number,
                                    "last_update_on": current_time,
                                }
                            },
                            upsert=True,
                        )
                    )
                case None:
                    # Skip invalid peer data
                    continue

        if bulk_ops:
            await self._peer.bulk_write(bulk_ops)

    async def update_usernames(self, usernames: list[UsernameTuple]) -> None:
        """
        Update the usernames table with the provided information.

        Parameters:
            usernames: A list of tuples containing the information of the usernames to be updated.
                Each tuple must contain: (peer_id, list_of_usernames)
        """
        if not usernames:
            return

        current_time = int(time.time())
        bulk_ops = []

        for peer_id, username_list in usernames:
            # Remove existing usernames for this peer
            await self._usernames.delete_many({"peer_id": peer_id})

            # Batch insert new usernames
            bulk_ops.extend(
                UpdateOne(
                    {"peer_id": peer_id, "username": username},
                    {
                        "$set": {
                            "peer_id": peer_id,
                            "username": username,
                            "last_update_on": current_time,
                        }
                    },
                    upsert=True,
                )
                for username in username_list
            )

        if bulk_ops:
            await self._usernames.bulk_write(bulk_ops)

    async def update_state(
        self, update_state: UpdateStateTuple = _SENTINEL
    ) -> UpdateStateTuple | None:
        """
        Get or set the update state of the current session.

        Parameters:
            update_state: A tuple containing the update state to set.
                Tuple must contain: (id, pts, qts, date, seq)
        """
        match update_state:
            case _ if update_state is _SENTINEL:
                # Get current state
                if data := await self._session.find_one(
                    {"_id": 0}, {"update_state": 1}
                ):
                    if "update_state" in data:
                        return tuple(data["update_state"])
                return None

            case _:
                # Set new state
                await self._session.update_one(
                    {"_id": 0},
                    {"$set": {"update_state": list(update_state)}},
                    upsert=True,
                )
                return None

    async def get_peer_by_id(self, peer_id: int) -> InputPeer:
        """
        Retrieve a peer by its ID.

        Parameters:
            peer_id: The ID of the peer to retrieve.
        """
        match await self._peer.find_one(
            {"_id": peer_id}, {"_id": 1, "access_hash": 1, "type": 1}
        ):
            case None:
                raise KeyError(f"ID not found: {peer_id}")
            case res:
                return get_input_peer(*res.values())

    async def get_peer_by_username(self, username: str) -> InputPeer:
        """
        Retrieve a peer by its username.

        Parameters:
            username: The username of the peer to retrieve.
        """
        # Find username in usernames collection
        match await self._usernames.find_one(
            {"username": username}, {"peer_id": 1, "last_update_on": 1}
        ):
            case None:
                raise KeyError(f"Username not found: {username}")
            case username_res:
                # Check TTL
                if (
                    abs(time.time() - username_res["last_update_on"])
                    > self.USERNAME_TTL
                ):
                    raise KeyError(f"Username expired: {username}")

                # Get peer info from peers collection
                peer_id = username_res["peer_id"]
                match await self._peer.find_one(
                    {"_id": peer_id}, {"_id": 1, "access_hash": 1, "type": 1}
                ):
                    case None:
                        raise KeyError(f"Peer not found for username: {username}")
                    case res:
                        return get_input_peer(
                            res["_id"], res["access_hash"], res["type"]
                        )

    async def get_peer_by_phone_number(self, phone_number: str) -> InputPeer:
        """
        Retrieve a peer by its phone number.

        Parameters:
            phone_number: The phone number of the peer to retrieve.
        """
        match await self._peer.find_one(
            {"phone_number": phone_number}, {"_id": 1, "access_hash": 1, "type": 1}
        ):
            case None:
                raise KeyError(f"Phone number not found: {phone_number}")
            case res:
                return get_input_peer(*res.values())

    async def _get(self) -> Any | None:
        """Internal method to get session attributes."""
        attr = inspect.stack()[2].function
        match await self._session.find_one({"_id": 0}, {attr: 1}):
            case None:
                return None
            case data:
                return data.get(attr)

    async def _set(self, value: Any) -> None:
        """Internal method to set session attributes."""
        attr = inspect.stack()[2].function
        await self._session.update_one({"_id": 0}, {"$set": {attr: value}}, upsert=True)

    async def _accessor(self, value: Any = _SENTINEL) -> Any:
        """Internal accessor method for session attributes."""
        match value:
            case _ if value is _SENTINEL:
                return await self._get()
            case _:
                return await self._set(value)

    async def dc_id(self, value: int = _SENTINEL) -> int | None:
        """Get or set the DC ID of the current session."""
        return await self._accessor(value)

    async def api_id(self, value: int = _SENTINEL) -> int | None:
        """Get or set the API ID of the current session."""
        return await self._accessor(value)

    async def test_mode(self, value: bool = _SENTINEL) -> bool | None:
        """Get or set the test mode of the current session."""
        return await self._accessor(value)

    async def auth_key(self, value: bytes = _SENTINEL) -> bytes | None:
        """Get or set the authorization key of the current session."""
        return await self._accessor(value)

    async def date(self, value: int = _SENTINEL) -> int | None:
        """Get or set the date of the current session."""
        return await self._accessor(value)

    async def user_id(self, value: int = _SENTINEL) -> int | None:
        """Get or set the user ID of the current session."""
        return await self._accessor(value)

    async def is_bot(self, value: bool = _SENTINEL) -> bool | None:
        """Get or set the bot flag of the current session."""
        return await self._accessor(value)

    async def export_session_string(self) -> str:
        """
        Exports the session string for the current session.

        Returns:
            The session string for the current session.
        """
        # Use asyncio.gather for concurrent attribute retrieval
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
