"""
Constants in the CD audio world.
"""
from base64 import b64encode
from enum import auto, Enum, IntFlag
from hashlib import sha1
from typing import NamedTuple, Tuple

SECONDS_PER_MINUTE = 60
FRAMES_PER_SECOND = 75
FRAMES_PER_MINUTE = FRAMES_PER_SECOND * SECONDS_PER_MINUTE
BYTES_PER_FRAME = 2048      # Bytes per frame without error correction headers
BYTES_PER_FRAME_RAW = 2352  # Bytes per frame with error correction headers
GAP_FRAMES = 150            # Standard leadin gap size

TRACK_MAX = 99
INDEX_MAX = 99

LEADOUT_TRACK = 0xAA        # Leadout track identifier

class TrackType(Enum):
    """
    The type of track on a CD (audio, data, or the leadout track).
    """
    audio = auto()
    data = auto()
    leadout = auto()

class TrackFlags(IntFlag):
    """
    Flags applied to a track.
    """
    # pylint: disable=C0326
    QUAD_CHANNEL =      0b1000  # Audio tracks only
    DATA_TRACK =        0b0100
    COPY_PERMITTED =    0b0010
    PREEMPHASIS =       0b0001  # Audio tracks -- preemphasis applied
    INCREMENTAL =       0b0001  # Data tracks -- data recorded incrementally

class TrackInformation(NamedTuple):
    """
    Information about a track.
    """
    track: int              # LEADOUT_TRACK (0xAA) if this is the leadout
    track_type: TrackType
    flags: TrackFlags
    start_frame: int

class DiscInformation(NamedTuple):
    """
    Information about the tracks on a disc.
    """
    first_track: int
    last_track: int
    track_information: Tuple[TrackInformation, ...]

    @property
    def musicbrainz_id(self) -> str:
        """
        The MusicBrainz disc ID.
        """
        leadout = self.track_information[-1]
        assert leadout.track_type == TrackType.leadout

        hasher = sha1(
            f"{self.first_track:02X}{self.last_track:02X}"
            f"{leadout.start_frame + GAP_FRAMES:08X}"
            .encode("ascii"))

        n_audio_tracks = 0
        for track in self.track_information:
            if track.track_type == TrackType.audio:
                hasher.update(
                    f"{track.start_frame + GAP_FRAMES:08X}".encode("ascii"))
                n_audio_tracks += 1

        # We always encode 99 track offsets; the remainder are 0.
        for _ in range(n_audio_tracks, 99):
            hasher.update(b"00000000")

        return (
            b64encode(hasher.digest(), altchars=b"._").replace(b"=", b"-")
            .decode("ascii"))

class MSF(NamedTuple):
    """
    Position on a disc specified in minutes, seconds, and frames.
    """
    minute: int
    second: int
    frame: int

    @property
    def lba(self) -> int:
        """
        Returns this MSF position to a logical block address (LBA) -- i.e.
        pure frame count.
        """
        return (self.minute * FRAMES_PER_MINUTE +
                self.second * FRAMES_PER_SECOND +
                self.frame)

    @property
    def is_valid(self):
        """
        Indicates whether this position is valid: all fields are non-negative,
        frame < 75, and second < 60.
        """
        # pylint: disable=C0122
        return (0 <= self.minute and
                0 <= self.second < SECONDS_PER_MINUTE and
                0 <= self.frame < FRAMES_PER_SECOND)

    @staticmethod
    def from_lba(frame: int) -> "MSF":
        """
        Convert a logical block address (in frames) to MSF.
        """
        minute, frame = divmod(frame, FRAMES_PER_MINUTE)
        second, frame = divmod(frame, FRAMES_PER_SECOND)
        return MSF(minute=minute, second=second, frame=frame)

class TrackIndex:
    """
    Position on a disc specified in track and index.
    """
    __slots__ = ("_track", "_index")

    def __init__(self, track: int, index: int) -> None:
        super(TrackIndex, self).__init__()
        if not isinstance(track, int):
            raise TypeError("track must be an int")

        if not 0 <= track <= TRACK_MAX:
            raise ValueError(
                f"track must be between 0 and {TRACK_MAX}, inclusive: {track}")

        if not isinstance(index, int):
            raise TypeError("index must be an int")

        if not 0 <= index <= INDEX_MAX:
            raise ValueError(
                f"index must be between 0 and {INDEX_MAX}, inclusive: {index}")

        self._track = track
        self._index = index

    @property
    def track(self) -> int:
        """
        The track on the disc.
        """
        return self._track

    @property
    def index(self) -> int:
        """
        The index within the track.
        """
        return self._index

    def __repr__(self) -> str:
        return f"TrackIndex(track={self.track}, index={self.index})"
