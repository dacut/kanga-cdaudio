"""
Operating system agnotic drive control and CD data.
"""
# pylint: disable=C0103

from enum import Enum, auto
import os
from platform import system
from typing import TypeVar, Type
from .cd import DiscInformation, MSF, TrackInformation

class DriveStatus(Enum):
    """
    Status of a drive (or slot on a multi-slot drive).
    """
    ok = auto()
    unknown = auto()
    no_disc = auto()
    tray_open = auto()
    not_ready = auto()

T = TypeVar("T", bound="CDROMDrive")
class CDROMDrive:
    """
    Class for interacting with a compact disc drive.

    Certain get_*() methods are not properties if they can involve mechanical
    actions (e.g. spinning up a disc) to make users aware these are potentially
    expensive actions.
    """
    def __new__(cls, *_, **__):
        if cls == CDROMDrive:
            plat = system()
            if plat == "Linux":
                from .linux import LinuxCDROMDrive # pylint: disable=R0401
                concrete = LinuxCDROMDrive
            else:
                raise RuntimeError(
                    f"Cannot instantiate CDROMDrive on platform {plat}")
        else:
            concrete = cls

        return super(CDROMDrive, cls).__new__(concrete)

    def __init__(self, handle: int, owned: bool) -> None:
        super(CDROMDrive, self).__init__()
        self._handle: int = handle
        self._owned: bool = owned

    def __del__(self) -> None:
        if self._owned and self._handle >= 0:
            os.close(self._handle)
            self._handle = -1

    def play(self) -> None:
        """
        Start or resume audio playback.
        """
        raise NotImplementedError()

    def pause(self) -> None:
        """
        Pause audio playback.
        """
        raise NotImplementedError()

    def stop(self) -> None:
        """
        Stop audio playback and spin down the disc.
        """
        raise NotImplementedError()

    def seek(self, position: MSF) -> None:
        """
        Seek to the specified position on the disc.
        """
        raise NotImplementedError()

    def eject(self) -> None:
        """
        Eject the CD from the drive or current slot.

        On some drives, this may close an open tray.
        """
        raise NotImplementedError()

    def close_tray(self) -> None:
        """
        Close an open tray.
        """
        raise NotImplementedError()

    def lock(self) -> None:
        """
        Lock the tray/disc so it cannot be ejected.
        """
        raise NotImplementedError()

    def unlock(self) -> None:
        """
        Unlock the tray/disc so it can be ejected.
        """
        raise NotImplementedError()

    def reset(self) -> None:
        """
        Reset the drive. The exact interpretation of this command is system
        and drive dependent, and may require elevated privileges.
        """
        raise NotImplementedError()

    @property
    def slot_count(self) -> int:
        """
        Return the number of slots in the drive. If the drive is not a jukebox,
        this returns 1.
        """
        return self._get_slot_count()

    def _get_slot_count(self) -> int:
        raise NotImplementedError()

    def select_slot(self, value: int) -> None: # pylint: disable=R0201
        """
        Sets the current slot for the drive (if supported).
        """
        raise ValueError("Changing slots not supported on this device")

    def get_status(self) -> DriveStatus:
        """
        Return the current status of the drive/selected slot.
        """
        raise NotImplementedError()

    def get_disc_information(self) -> DiscInformation:
        """
        Return metadata about the currently inserted disc.
        """
        raise NotImplementedError()

    def get_track_information(self, track: int) -> TrackInformation:
        """
        Return information about the specified track. track is the CD-based
        identifier of the track, starting at disc_information.first_track
        (usually 1, not 0!), or LEADOUT_TRACK (0xAA) to get information about
        the end-of-disc.
        """
        raise NotImplementedError()

    @property
    def handle(self) -> int:
        """
        The integer file descriptor used to manage the drive.
        """
        return self._handle

    @property
    def owned(self) -> bool:
        """
        Whether we own the file descriptor (and will close it when this object
        is destroyed).
        """
        return self._owned

    @classmethod
    def from_filename(cls: Type[T], filename: str) -> T:
        """
        Create a CDROMDrive object by opening the specified filename.
        """
        fd = os.open(filename, os.O_RDONLY)
        try:
            return cls(fd, True)
        except:
            os.close(fd)
            raise
