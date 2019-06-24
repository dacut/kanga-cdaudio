#!/usr/bin/env python3
"""\
Usage: ripper.py [options]
Rip a CD and upload its contents and metadata to DynamoDB/S3.

Options:
    -c <filename> | --config <filename>
        Read configuration data from the specifed file. Defaults to ripper.conf.

    -h | --help
        Show this usage information.

    -p <name> | --profile <name>
        Use the specified profile for AWS credentials.

    -r <name> | --region <name>
        Use the specified region.

Configuration file:
The configuration file is an INI-style file with the following options:

[musicbrainz]
username = <str> # Usually not needed
password = <str> # Usually not needed

# User-agent to use for MusicBrainz; defaults to
# kanga-cdlogic-ripper/0.1.0 ( dacut@kanga.org )
user_agent = <str>

# Maximum number of calls/second to use for MusicBrainz; defaults to 1.0
rate_limit = <float>

# Country codes to prefer for releases; defaults to US,CA,GB,AU,NZ
country_preference = <str>,<str>,...

[aws]
s3_bucket = <str> # Defaults to <account-id>-music-collection
s3_prefix = <str> # Optional; defaults to the empty string
"""

from concurrent.futures import Future, ThreadPoolExecutor
from configparser import ConfigParser
from getopt import getopt, GetoptError
import json
from logging import getLogger, basicConfig, DEBUG, WARNING
from os import chdir, getcwd
from os.path import exists
from re import compile as re_compile
from subprocess import run, PIPE
from sys import argv, exit, stderr, stdout # pylint: disable=W0622
from tempfile import mkdtemp
from typing import Any, Dict, List, Optional, Sequence, Set

from boto3.session import Session
import musicbrainzngs as mb

from kanga.cdaudio.drive import CDROMDrive
from kanga.cdaudio.cd import TrackType

# pylint: disable=C0103,R0902,R0913,R0914,R0915

VERSION = "0.1.0"

MB_INCLUDES = [
    "artists", "labels", "recordings", "release-groups", "artist-credits",
    "isrcs", "aliases", "area-rels", "artist-rels", "label-rels", "place-rels",
    "event-rels", "recording-rels", "release-rels", "release-group-rels",
    "series-rels", "url-rels", "work-rels", "instrument-rels"]

DEFAULT_USER_AGENT = f"kanga-cdlogic-ripper/{VERSION} ( dacut@kanga.org )"
DEFAULT_COUNTRY_PREFERENCE = ("US", "CA", "GB", "AU", "NZ")
LOG_FORMAT = (
    "%(asctime)s %(threadName)s %(name)s [%(levelname)s] "
    "%(filename)s %(lineno)d: %(message)s")

USER_AGENT_RE = re_compile(
    r"^\s*(?P<app>[^/]+)/"
    r"(?P<ver>[^\s]+)\s+"
    r"(?:\(\s*(?P<contact>[^\s)]+)\s*\))?\s*$")

log = getLogger(__name__)

class RipperConfig:
    """
    Configuration settings for the Kanga CDLogic Ripper.
    """

    def __init__(
                self,
                aws_region: Optional[str] = None,
                aws_profile: Optional[str] = None,
                s3_bucket_name: Optional[str] = None,
                s3_prefix: str = "",
                musicbrainz_username: Optional[str] = None,
                musicbrainz_password: Optional[str] = None,
                musicbrainz_rate_limit: float = 1.0,
                musicbrainz_user_agent: str = DEFAULT_USER_AGENT,
                musicbrainz_country_preference: Sequence[str] = DEFAULT_COUNTRY_PREFERENCE) -> None:
        super(RipperConfig, self).__init__()
        self.aws_region = aws_region
        self.aws_profile = aws_profile
        self.s3_bucket_name = s3_bucket_name
        self.s3_prefix = s3_prefix
        self.musicbrainz_username = musicbrainz_username
        self.musicbrainz_password = musicbrainz_password
        self.musicbrainz_rate_limit = musicbrainz_rate_limit
        self.musicbrainz_user_agent = musicbrainz_user_agent
        self.musicbrainz_country_preference = musicbrainz_country_preference

    def parse_config(self, filename: str) -> None:
        """
        Configure values from a configuration file.
        """
        cp = ConfigParser()
        cp.read(filename)
        self.parse_configparser(cp)

    def parse_configparser(self, cp: ConfigParser) -> None:
        """
        Configure values from a ConfigParser instance.
        """
        profile = cp.get("aws", "profile", fallback=None) # type: ignore
        if profile is not None:
            self.aws_profile = profile

        region = cp.get("aws", "region", fallback=None) # type: ignore
        if region is not None:
            self.aws_region = region

        s3_bucket_name = cp.get("aws", "s3_bucket", fallback=None) # type: ignore
        if s3_bucket_name is not None:
            self.s3_bucket_name = s3_bucket_name

        s3_prefix = cp.get("aws", "s3_prefix", fallback=None) # type: ignore
        if s3_prefix is not None:
            self.s3_prefix = s3_prefix

        username = cp.get("musicbrainz", "username", fallback=None) # type: ignore
        password = cp.get("musicbrainz", "password", fallback=None) # type: ignore
        if username is not None and password is not None:
            self.musicbrainz_username = username
            self.musicbrainz_password = password

        rate_limit = cp.get("musicbrainz", "rate_limit", fallback=None) # type: ignore
        if rate_limit is not None:
            self.musicbrainz_rate_limit = float(rate_limit)

        user_agent = cp.get("musicbrainz", "user_agent", fallback=None) # type: ignore
        if user_agent is not None:
            self.musicbrainz_user_agent = user_agent
        country_pref = cp.get( # type: ignore
            "musicbrainz", "country_preference", fallback=None)
        if country_pref is not None:
            self.musicbrainz_country_preference = [
                country.strip().upper() for country in country_pref.split(",")]

    def configure_musicbrainz(self) -> None:
        """
        Configure the MusicBrainz library global settings using the values
        from this RipperConfig instance.
        """
        interval = 1.0 / self.musicbrainz_rate_limit
        log.info("Setting MusicBrainz ratelimit to interval=%g calls=1",
                 interval)
        mb.set_rate_limit(interval, 1)

        m = USER_AGENT_RE.match(self.musicbrainz_user_agent)
        if not m:
            raise ValueError(
                f"Invalid useragent format: expected app/version "
                f"( email@ domain ): {repr(self.musicbrainz_user_agent)}")
        app = m.group("app")
        ver = m.group("ver")
        contact: Optional[str] = m.group("contact")
        contact = contact if contact else None

        log.info("Setting MusicBrainz user-agent to app=%r ver=%r contact=%r",
                 app, ver, contact)
        mb.set_useragent(app, ver, contact)

        if self.musicbrainz_username and self.musicbrainz_password:
            mb.auth(self.musicbrainz_username, self.musicbrainz_password)

    def get_boto_session(self) -> Session:
        """
        Create a Boto3 session based on the region and profile specified in
        this RipperConfig instance.
        """
        boto_kw = {}
        if self.aws_region:
            boto_kw["region_name"] = self.aws_region

        if self.aws_profile:
            boto_kw["profile_name"] = self.aws_profile

        log.info("Creating Boto3 session with config=%s", boto_kw)
        return Session(**boto_kw)

    @staticmethod
    def get_default_bucket_name(boto: Session) -> str:
        """
        Return a default bucket name for an account in the form:
        <account_id>-music-collection
        """
        sts = boto.client("sts")
        cid = sts.get_caller_identity()
        return f'{cid["Account"]}-music-collection'

class Ripper:
    """
    Control the CD ripping process.
    """

    def __init__(self, config: RipperConfig, cdrom_filename: str = "/dev/cdrom") -> None:
        super(Ripper, self).__init__()
        self.config = config
        self.config.configure_musicbrainz()
        self.boto = config.get_boto_session()
        self.s3 = self.boto.resource("s3")

        self.cdrom_filename = cdrom_filename
        self.drive = CDROMDrive.from_filename(cdrom_filename)
        self.disc_info = self.drive.get_disc_information()
        self.disc_id = self.disc_info.musicbrainz_id
        self.disc_metadata: Dict[str, Any] = {}

        if self.config.s3_bucket_name is None:
            self.config.s3_bucket_name = RipperConfig.get_default_bucket_name(
                self.boto)
        self.bucket = self.s3.Bucket(self.config.s3_bucket_name)

        self.executor = ThreadPoolExecutor()

        # Set defaults for the release, medium, etc.
        self.release: Dict[str, Any] = {}
        self.medium: Dict[str, Any] = {}
        self.disc_index = 1
        self.tracks: Dict[int, Dict[str, Any]] = {}

    def ensure_bucket_exists(self) -> None:
        """
        Ensure the S3 bucket exists, creating it if necessary.
        """
        if self.bucket.creation_date is not None:
            log.info("S3 bucket %s exists", self.bucket.name)
            return

        if self.boto.region_name == "us-east-1":
            kw: Dict[str, Any] = {}
        elif self.boto.region_name == "eu-west-1":
            kw = {"CreateBucketConfiguration": {"LocationConstraint": "EU"}}
        else:
            kw = {"CreateBucketConfiguration": {
                "LocationConstraint": self.boto.region_name}}

        log.info("Creating S3 bucket %s with config %s", self.bucket.name, kw)
        self.bucket.create(ACL="private", **kw)
        self.bucket.wait_until_exists()

        # We need an S3 client (not resource) to call put_public_access_block
        s3_c = self.boto.client("s3")

        s3_c.put_public_access_block(
            Bucket=self.config.s3_bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        )

    def rank_release_by_country(self, release: Dict[str, Any]) -> int:
        """
        Given a MusicBrainz release structure, examine its country and return
        an integer indicating the user's preference for using these details.
        """
        country = release.get("country", "").upper()
        if country not in self.config.musicbrainz_country_preference:
            return len(self.config.musicbrainz_country_preference)

        return self.config.musicbrainz_country_preference.index(country)

    def get_preferred_names(self) -> None:
        """
        Set the release, medium, disc_index, and tracks members using the
        preferred release.
        """
        # Order releases by preferred country
        releases_by_country = sorted(
            self.disc_metadata["disc"]["release-list"],
            key=self.rank_release_by_country)

        for release in releases_by_country:
            country = release["country"]
            # Which medium do we have?
            for medium in release["medium-list"]:
                disc_index = int(medium["position"])
                disc_ids = [disc["id"] for disc in medium["disc-list"]]

                log.debug("Searching for disc id %s in %s release, medium %d, "
                          "containing disc ids %s", self.disc_id, country,
                          disc_index, disc_ids)

                if self.disc_id not in disc_ids:
                    log.debug("Disc id not found")
                    continue

                # Found it
                log.debug("Disc id found")
                self.release = release
                self.medium = medium
                self.disc_index = disc_index
                self.tracks = {
                    int(track["number"]): track
                    for track in medium["track-list"]
                }
                return

        # Nothing found. <sigh>
        log.error("Did not find disc id %s in any release/medium", self.disc_id)

    def put_object(self, Key: str, **kw):
        """
        Asynchronously write an object to S3.
        """
        def task():
            try:
                self.log.debug("Writing s3://%s/%s", self.bucket.name, Key)
                result = self.bucket.put_object(Key=Key, **kw)
                self.log.debug(
                    "Write of s3://%s/%s succeeded", self.bucket.name, Key)
                return result
            except:
                self.log.error(
                    "Write of s3://%s/%s failed", self.bucket.name, Key,
                    exc_info=True)
                raise

        self.executor.submit(task)

    def get_album_art(self) -> None:
        """
        Get album art for each release. This modifies the release structure
        to include an images field.
        """
        # Record the IDs of images we've seen in case they're shared across
        # releases.
        seen_images: Set[str] = set()

        # Record how many images we've seen of each type so we can number any
        # images beyond the first.
        image_type_counts: Dict[str, int] = {}

        for release in self.disc_metadata["disc"]["release-list"]:
            rel_id = release["id"]
            log.debug("Getting images for release id %s", rel_id)

            try:
                il_result = mb.get_image_list(rel_id)
                image_list = il_result["images"]

                for image_info in image_list:
                    image_id = image_info["id"]
                    if image_id in seen_images:
                        # Don't re-download images we've seen before.
                        log.debug("Skipping already-seen image %s", image_id)
                        continue

                    # image_types will usually be a single element list, e.g.:
                    # ["Front"], ["Back"], but occasionally contains multiple
                    # items: ["Back", "Spine"].
                    image_types = image_info.get("types", [])

                    if image_types:
                        image_type = "-".join(image_types)
                    else:
                        image_type = "Unknown"

                    image_type_count = image_type_counts.get(image_type, 0) + 1
                    image_type_counts[image_type] = image_type_count

                    # If this is the first image, don't include a -# suffix.
                    key = (
                        f"{self.config.s3_prefix}{self.disc_id}/"
                        f"{image_type.lower()}")
                    if image_type_count > 1:
                        key += f"-{image_type_count}"
                    key += ".jpg"
                    image_info["local"] = f"s3://{self.bucket.name}{key}"

                    log.debug("Image %s: type=%s key=%s", image_id, image_type,
                              key)

                    # Download the image from the Cover Art Archive, then
                    # write it to S3.
                    def copy_art_to_s3(image_id, rel_id, key):
                        nonlocal self
                        image = mb.get_image(rel_id, image_id)
                        self.bucket.put_object(
                            ACL="private", Body=image, ContentType="image/jpeg",
                            Key=key)
                    self.executor.submit(copy_art_to_s3, image_id, rel_id, key)
                release["images"] = image_list
            except mb.musicbrainz.ResponseError:
                release["images"] = []

    def rip_convert_track(self, track_index: int) -> None:
        """
        Rip a track using cdparanoia. Convert it to FLAC, AAC, and MP3 formats.
        Upload it to S3.
        """
        cdparanoia_log_filename = f"cdparanoia-{track_index:02d}.log"
        wav_filename = f"track-{track_index:02d}.wav"
        cmd = [
            "cdparanoia", "--force-cdrom-device", self.cdrom_filename,
            f"--log-debug={cdparanoia_log_filename}", str(track_index),
            wav_filename]
        log.debug("Executing %s", " ".join(cmd))
        cp = run(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)

        # Wait until cdparanoia finishes
        if cp.returncode != 0:
            log.error("cdparanoia on track %d failed: exit code %d",
                      track_index, cp.returncode)

            with open(cdparanoia_log_filename, "r") as fd:
                for line in fd:
                    log.error("%s", line)
            return

        with open(cdparanoia_log_filename, "rb") as bfd:
            self.put_object(
                ACL="private", Body=bfd.read(), ContentType="text/plain",
                Key=(f"{self.config.s3_prefix}{self.disc_id}/"
                     f"{cdparanoia_log_filename}"))

        self.convert_upload_flac(track_index)

    def convert_upload_flac(self, track_index: int) -> None:
        """
        Convert a WAV file to FLAC, adding tags, and upload it to S3.
        """
        output_filename = f"track-{track_index:02d}.flac"
        cmd = ["flac", "-5", f"--output-name={output_filename}"]
        track = self.tracks.get(track_index, {})
        track_total = (
            len(self.tracks) if self.tracks
            else len(self.disc_info.track_information) - 1)
        recording = track.get("recording", {})
        label = ((self.release.get("label-info-list", []) + [{}])[0]
                 .get("label", {}).get("name"))
        release_group = self.release.get("release-group", {})

        cmd.append(f"--tag=DISCNUMBER={self.disc_index}")
        cmd.append(f"--tag=DISCTOTAL={self.release['medium-count']}")
        cmd.append(f"--tag=TRACKNUMBER={track_index}")
        cmd.append(f"--tag=TRACKTOTAL={track_total}")

        album_title = self.release.get("title")
        if album_title:
            cmd.append(f"--tag=ALBUM={album_title}")

        if label:
            cmd.append(f"--tag=LABEL={label}")

        disambiguation = self.release.get("disambiguation")
        if disambiguation:
            cmd.append(f"--tag=VERSION={disambiguation}")

        date = self.release.get("date")
        if date:
            cmd.append(f"--tag=DATE={date}")

        barcode = self.release.get("barcode")
        if barcode:
            cmd.append(f"--tag=EAN/UPN={barcode}")

        asin = self.release.get("asin")
        if asin:
            cmd.append(f"--tag=ASIN={asin}")

        for url in self.release.get("url-relation-list", []):
            tag = f"URL_{url['type'].replace(' ', '_').upper()}"
            cmd.append(f"--tag={tag}={url['target']}")

        genres = release_group.get("secondary-type-list", [])
        for genre in genres:
            cmd.append(f"--tag=GENRE={genre}")

        medium_format = self.medium.get("format")
        if medium_format:
            cmd.append(f"--tag=SOURCEMEDIA={medium_format}")

        track_title = recording.get("title")
        if track_title:
            cmd.append(f"--tag=TITLE={track_title}")

        artist = track.get("artist-credit-phrase")
        if artist:
            cmd.append(f"--tag=ARTIST={artist}")

        performer = recording.get("artist-credit-phrase")
        if performer:
            cmd.append(f"--tag=PERFORMER={performer}")
        
        cmd.append(f"track-{track_index:02d}.wav")

        s3_key = f"{self.config.s3_prefix}{self.disc_id}/{track_index:02d}.flac"

        def task():
            nonlocal cmd, output_filename, self, s3_key
            log.info("Converting track %d to FLAC: %s", track_index,
                     " ".join(cmd))
            cp = run(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)
            if cp.returncode != 0:
                log.error("FLAC conversion of track %d failed: exit code %d",
                          track_index, cp.returncode)

                for line in cp.stderr.split("\n"):
                    log.error("%s", line)

                raise RuntimeError("FLAC conversion failed")

            log.info("Uploading %s to s3://%s/%s", output_filename,
                     self.bucket.name, s3_key)
            self.bucket.upload_file(output_filename, s3_key)
            log.info("Upload of %s done", output_filename)

        self.executor.submit(task)

    def rip_cd(self) -> None:
        """
        Rip a CD, uploading it to S3.
        """
        self.ensure_bucket_exists()

        tempdir = mkdtemp(prefix="cdrip-")
        log.info("Executing in %s", tempdir)
        old_wd = getcwd()
        chdir(tempdir)
        try:
            self._rip_cd_in_tmpdir()
        finally:
            log.info("Waiting for tasks to complete")
            self.executor.shutdown()
            chdir(old_wd)

    def _rip_cd_in_tmpdir(self) -> None:
        """
        Rip a CD; this requires the S3 bucket be created and the working
        directory be clean for our use.
        """
        # Get the MusicBrainz metadata
        self.disc_metadata = mb.get_releases_by_discid(
            self.disc_id, includes=MB_INCLUDES)
        self.get_preferred_names()

        # Get album art for each release found. This modifies the release
        # structure of the MusicBrainz metadata, so we need to call it first
        # before uploading that.
        self.get_album_art()

        # Now upload the MusicBrainz metadata.
        self.put_object(
            ACL="private", Body=json.dumps(self.disc_metadata).encode("utf-8"),
            ContentType="application/json",
            Key=f"{self.config.s3_prefix}{self.disc_id}/musicbrainz.json")

        # Start ripping each track. Don't execute cdparanoia in parallel,
        # though.
        for track in self.disc_info.track_information:
            if track.track_type != TrackType.audio:
                continue

            self.rip_convert_track(track.track)


def main(args: List[str]) -> int:
    """
    Main entrypoint for the application.
    """
    basicConfig(format=LOG_FORMAT, level=DEBUG)
    getLogger("botocore").setLevel(WARNING)
    getLogger("boto3").setLevel(WARNING)
    getLogger("musicbrainzngs").setLevel(WARNING)
    getLogger("urllib3").setLevel(WARNING)
    getLogger("s3transfer").setLevel(WARNING)
    config = RipperConfig()
    config_filename = None

    try:
        opts, args = getopt(args, "c:hp:r:", ["help"])
        for opt, val in opts:
            if opt in ("-h", "--help",):
                usage(stdout)
                return 0
            if opt in ("-c", "--config"):
                config_filename = val
            if opt in ("-p", "--profile"):
                config.aws_profile = val
            if opt in ("-r", "--region"):
                config.aws_region = val
        if args:
            print(f"Unknown argument {args[0]}", file=stderr)
            usage()
            return 1
    except GetoptError as e:
        print(str(e), file=stderr)
        usage()
        return 1

    if config_filename:
        config.parse_config(config_filename)
    elif exists("ripper.conf"):
        config.parse_config("ripper.conf")

    ripper = Ripper(config)
    ripper.rip_cd()

    return 0

def usage(fd=stderr):
    """
    Print usage information to the specified file handle.
    """
    fd.write(__doc__)

if __name__ == "__main__":
    exit(main(argv[1:]))
