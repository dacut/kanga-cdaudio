"""
Linux-specific functionality.
"""
from ctypes import (
    CDLL, byref, c_int, c_uint8, c_ulong, get_errno, Structure, Union)
from os import strerror
from typing import Any, Optional

from .cd import (
    DiscInformation, LEADOUT_TRACK, MSF, TrackFlags, TrackIndex,
    TrackInformation, TrackType
)
from .drive import CDROMDrive, DriveStatus

# From linux/cdrom.h
CDROMPAUSE = 0x5301
CDROMRESUME = 0x5302
CDROMPLAYMSF = 0x5303
CDROMPLAYTRKIND = 0x5304
CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROMSTOP = 0x5307
CDROMEJECT = 0x5309
CDROMRESET = 0x5312
CDROMSEEK = 0x5316
CDROMCLOSETRAY = 0x5319
CDROM_SELECT_DISC = 0x5323
CDROM_MEDIA_CHANGED = 0x5325
CDROM_DRIVE_STATUS = 0x5326
CDROM_DISC_STATUS = 0x5327
CDROM_CHANGER_NSLOTS = 0x5328
CDROM_LOCKDOOR = 0x5329
CDROM_GET_CAPABILITY = 0x5331

# CD-ROM address types -- cdrom_tocentry.cdte_format
CDROM_LBA = 0x01 # Logical block address; first frame is 0.
CDROM_MSF = 0x02 # Minute/Second/Frame; binary, not BCD.

# CD-ROM track types -- cdrom_tocentry.cdte_ctrl
CDROM_DATA_TRACK = 0x04

# Drive status from CDROM_DRIVE_STATUS ioctl
CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4

# Disc status from CDROM_DISC_STATUS ioctl; can return drive status as well.
CDS_AUDIO = 100
CDS_DATA_1 = 101
CDS_DATA_2 = 102
CDS_XA_2_1 = 103
CDS_XA_2_2 = 104
CDS_MIXED = 105

# Special slot codes
CDSL_NONE = c_int((1 << 31) - 2)
CDSL_CURRENT = c_int((1 << 31) - 1)

class cdrom_msf0(Structure):
    """
    Address in MSF format.
    """
    _fields_ = [
        ("minute", c_uint8),
        ("second", c_uint8),
        ("frame", c_uint8),
    ]

class cdrom_addr(Union):
    """
    Address in either MSF or logical format.
    """
    _fields_ = [
        ("msf", cdrom_msf0),
        ("lba", c_int),
    ]

class cdrom_tochdr(Structure):
    """
    Data returned by the CDROMREADTOCHDR ioctl.
    """
    _fields_ = [
        ("cdth_trk0", c_uint8),
        ("cdth_trk1", c_uint8),
    ]

class cdrom_tocentry(Structure):
    """
    Structure used by the CDROMREADTOCENTRY ioctl.
    """
    _fields_ = [
        ("cdte_track", c_uint8),
        # cdte_adr_ctl is actually a bitfield:
        #     cdte_adr in lower nybble, cdte_ctrl in upper nybble on x86.
        ("cdte_adr_ctrl", c_uint8),
        ("cdte_format", c_uint8),
        ("cdte_addr", cdrom_addr),
        ("cdte_datamode", c_uint8),
    ]

class LinuxCDROMDrive(CDROMDrive):
    """
    Linux-specific code for handling CD-ROM drives.
    """
    def __init__(self, handle: int, owned: bool) -> None:
        super(LinuxCDROMDrive, self).__init__(handle=handle, owned=owned)
        self._libc = CDLL("libc.so.6", use_errno=True)
        self._libc.ioctl.restype = c_int
    
    def ioctl(self, cmd: int, arg: Optional[Any] = None) -> int:
        if isinstance(arg, int):
            ioctl_arg: Union[c_ulong, "CArgObject"] = c_ulong(arg)
        elif arg is None:
            ioctl_arg = c_ulong(0)
        elif isinstance(arg, (Structure, Union)):
            ioctl_arg = byref(arg)

        result = self._libc.ioctl(c_int(self.handle), c_ulong(cmd), ioctl_arg)
        if result < 0:
            errno = get_errno()
            raise IOError(errno, strerror(errno))
        
        return result

    def play(self) -> None:
        self.ioctl(CDROMRESUME)
    
    def pause(self) -> None:
        self.ioctl(CDROMPAUSE)

    def stop(self) -> None:
        self.ioctl(CDROMSTOP)

    def seek(self, position: MSF) -> None:
        if not isinstance(position, MSF):
            raise TypeError("position must be an MSF instance")
        
        if not position.is_valid:
            raise ValueError("position is invalid")

        # XXX: CDROMSEEK is not actually implemented on Linux and always
        # returns EINVAL from the SCSI driver. The SG_IO SEEK command requires
        # root privileges.
        # msf = cdrom_msf()
        # self.ioctl(CDROMSEEK, msf)

        raise NotImplementedError("Linux does not implement seek")
    
    def eject(self) -> None:
        self.ioctl(CDROMEJECT)
    
    def close_tray(self) -> None:
        self.ioctl(CDROMCLOSETRAY)

    def lock(self) -> None:
        self.ioctl(CDROM_LOCKDOOR, 1)

    def unlock(self) -> None:
        self.ioctl(CDROM_LOCKDOOR, 0)
    
    def reset(self) -> None:
        self.ioctl(CDROMRESET)

    def _get_slot_count(self) -> int:
        return self.ioctl(CDROM_CHANGER_NSLOTS)

    def select_slot(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("slot must be an integer")
        
        if value < 0:
            raise ValueError("slot must be non-negative")
        
        self.ioctl(CDROM_SELECT_DISC, value)
    
    def _get_status(self) -> DriveStatus:
        status = self.ioctl(CDROM_DRIVE_STATUS, CDSL_CURRENT)
        if status == CDS_NO_DISC:
            return DriveStatus.no_disc
        if status == CDS_TRAY_OPEN:
            return DriveStatus.tray_open
        if status == CDS_DRIVE_NOT_READY:
            return DriveStatus.not_ready
        if status == CDS_DISC_OK:
            return DriveStatus.ok
        
        return DriveStatus.unknown

    def get_disc_information(self) -> DiscInformation:
        # Get the first and last track numbers
        tochdr = cdrom_tochdr()
        self.ioctl(CDROMREADTOCHDR, tochdr)

        first_track = tochdr.cdth_trk0.value
        last_track = tochdr.cdth_trk1.value
        track_information: List[TrackInformation] = []

        # Then iterate over the tracks
        for track in range(first_track, last_track + 1):
            track_information.append(self.get_track_information(track))

        # And get the leadout.
        track_information.append(self.get_track_information(LEADOUT_TRACK))
    
        return DiscInformation(first_track=first_track, last_track=last_track,
                               track_information=tuple(track_information))

    def get_track_information(self, track: int) -> TrackInformation:
        te = cdrom_tocentry()
        te.cdte_track = track
        te.cdte_adr_ctl = 0
        te.cdte_format = CDROM_LBA
        te.cdte_addr.lba = 0
        te.cdte_datamode = 0

        self.ioctl(CDROMREADTOCENTRY, te)
        
        # The control field minus the ADR bits.
        cdte_ctrl = (te.cdte_adr_ctrl & 0xf0) >> 4

        if track == LEADOUT_TRACK:
            track_type = TrackType.leadout
            flags = TrackFlags(0)
        elif cdte_ctrl & CDROM_DATA_TRACK:
            track_type = TrackType.data
            flags = TrackFlags(cdte_ctrl & 0x01)
        else:
            track_type = TrackType.audio
            flags = TrackFlags(cdte_ctrl)
        
        return TrackInformation(
            track=track, type=track_type, flags=flags,
            start_frame=te.cdte_addr.lba.value)
