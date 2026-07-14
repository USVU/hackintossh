#!/usr/bin/env python3
import argparse
import concurrent.futures
import logging
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:
    import requests
except ModuleNotFoundError:
    print("Missing required package. Install it with:")
    print("  pip install requests")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("liveatc")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
adapter = requests.adapters.HTTPAdapter(
    pool_connections=50, pool_maxsize=50, max_retries=2
)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

BASE_URL = "https://skylistening.com"
running = True

FEEDS = {
    # == JAPAN ==
    "rjtt_twr": "Tokyo Haneda Tower (118.100)",
    "rjtt_gnd": "Tokyo Haneda Ground (121.700)",
    "rjtt_app": "Tokyo Haneda Approach (119.100)",
    "rjtt_dep": "Tokyo Haneda Departure (120.800)",
    "rjtt_atis": "Tokyo Haneda ATIS",
    "rjaa_twr": "Tokyo Narita Tower (118.200)",
    "rjaa_app": "Tokyo Narita Approach (124.400)",
    "rjaa_dep": "Tokyo Narita Departure (124.200)",
    "rjaa_del": "Tokyo Narita Delivery (121.650)",
    # == USA - TOP AIRPORTS ==
    "kjfk_twr": "JFK Tower (119.100)",
    "kjfk_gnd": "JFK Ground (121.900)",
    "kjfk2_twr": "JFK Tower 2 (123.900)",
    "kjfk_app_127_4": "JFK Approach (127.400)",
    "kjfk_app_135_9": "JFK Departure (135.900)",
    "klax_twr": "LAX Tower (133.900)",
    "klax_gnd": "LAX Ground (121.600)",
    "klax_app_dep": "LAX Approach/Departure",
    "kord_twr": "Chicago O'Hare Tower (126.700)",
    "kord_gnd": "Chicago O'Hare Ground (121.900)",
    "kord_app": "Chicago O'Hare Approach (120.800)",
    "ksfo_twr": "SFO Tower (133.100)",
    "ksfo_gnd": "SFO Ground (121.800)",
    "ksfo_app_dep": "SFO Approach/Departure",
    "kdfw_twr": "Dallas/Ft Worth Tower (126.550)",
    "kdfw_gnd": "Dallas/Ft Worth Ground (121.650)",
    "kdfw_app": "Dallas/Ft Worth Approach (127.500)",
    "kdfw2_del": "Dallas/Ft Worth Delivery",
    "katl_twr": "Atlanta Tower (126.300)",
    "katl_gnd": "Atlanta Ground (121.700)",
    "katl_app": "Atlanta Approach (120.500)",
    "kden_twr": "Denver Tower (126.700)",
    "kden_gnd": "Denver Ground (121.700)",
    "kden_app": "Denver Approach (119.300)",
    "kphx_twr": "Phoenix Tower (118.100)",
    "kphx_gnd": "Phoenix Ground (121.900)",
    "kphx_dep": "Phoenix Departure (124.300)",
    "ksea_twr": "Seattle Tower (118.300)",
    "ksea_gnd": "Seattle Ground (121.700)",
    "ksea_app": "Seattle Approach (128.000)",
    "kbos_twr": "Boston Tower (119.300)",
    "kbos_gnd": "Boston Ground (121.700)",
    "kbos_app": "Boston Approach (119.900)",
    "kmia_twr": "Miami Tower (118.100)",
    "kmia_gnd": "Miami Ground (121.750)",
    "kmia_app": "Miami Approach (119.500)",
    "klas_twr": "Las Vegas Tower (119.900)",
    "klas_gnd": "Las Vegas Ground (121.800)",
    "kmco_twr": "Orlando Tower (118.600)",
    "kmco_gnd": "Orlando Ground (121.700)",
    "kphl_twr": "Philadelphia Tower (119.400)",
    "kphl_gnd": "Philadelphia Ground (121.700)",
    "kphl_app": "Philadelphia Approach (124.750)",
    "klga_twr": "LaGuardia Tower (119.100)",
    "klga_gnd": "LaGuardia Ground (121.650)",
    "kdca_twr": "Reagan National Tower (119.300)",
    "kdca_gnd": "Reagan National Ground (121.700)",
    "kbwi_twr": "Baltimore Tower (118.750)",
    "kbwi_gnd": "Baltimore Ground (121.650)",
    "kiad_twr": "Dulles Tower (119.300)",
    "kiad_gnd": "Dulles Ground (121.700)",
    "kiad1_del": "Dulles Delivery",
    # == CANADA ==
    "cyyz_twr": "Toronto Pearson Tower (118.700)",
    "cyyz_gnd": "Toronto Pearson Ground (121.900)",
    "cyvr_twr": "Vancouver Tower (118.700)",
    "cyvr_gnd": "Vancouver Ground (121.900)",
    "cyul_twr": "Montreal Tower (118.700)",
    "cyul_gnd": "Montreal Ground (121.900)",
    "cyul_app": "Montreal Approach",
    # == EUROPE ==
    "eham_del": "Amsterdam Schiphol Delivery (121.800)",
    # == CARIBBEAN ==
    "tjsj_twr": "San Juan Tower (118.100)",
    "tjsj_gnd": "San Juan Ground (121.700)",
    "tjsj_app": "San Juan Approach (128.200)",
}

FEED_REGIONS = {
    "rj": "Japan",
    "rjaa": "Japan",
    "rjtt": "Japan",
    "k": "USA",
    "cy": "Canada",
    "eh": "Europe",
    "ls": "Europe",
    "tj": "Caribbean",
    "tn": "Caribbean",
    "tt": "Caribbean",
}


def get_region(feed_id):
    if feed_id.startswith("rjtt") or feed_id.startswith("rjaa"):
        return "Japan"
    if feed_id.startswith("k"):
        return "USA"
    if feed_id.startswith("cy"):
        return "Canada"
    if feed_id.startswith("eh") or feed_id.startswith("ls"):
        return "Europe"
    return "Other"


def signal_handler(sig, frame):
    global running
    log.info("Shutting down...")
    running = False
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def sanitize_filename(s):
    return re.sub(r'[^\w.-]+', '_', s).strip('_')


def is_termux():
    return "com.termux" in os.environ.get("PREFIX", "") or shutil.which("termux-setup-package") is not None


def detect_player():
    if shutil.which("mpv"):
        return "mpv"
    if not is_termux() and shutil.which("ffplay"):
        return "ffplay"
    if shutil.which("mpg123"):
        return "mpg123"
    return None


def play_feed(feed_id, name, no_audio=False, output_dir=None, reconnect_delay=2, player=None):
    global running
    attempt = 0
    safe_name = sanitize_filename(name)
    stream_url = f"{BASE_URL}/api/atc/{feed_id}?t={int(time.time())}"

    while running:
        attempt += 1
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/liveatc",
            }

            log.info("[%s] Connecting... (attempt %d)", name, attempt)

            if no_audio:
                resp = SESSION.get(stream_url, headers=headers, stream=True, timeout=30)
                resp.raise_for_status()
                out_dir = output_dir or "."
                os.makedirs(out_dir, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = os.path.join(out_dir, f"{feed_id}_{safe_name}_{ts}.mp3")
                log.info("[%s] Saving to %s", name, out_path)
                with open(out_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if not running:
                            break
                        f.write(chunk)
                log.info("[%s] Saved %s bytes", name, os.path.getsize(out_path))
                attempt = 0
                if running:
                    log.warning("[%s] Stream ended, reconnecting...", name)
                continue

            if player == "mpv":
                cmd = [
                    "mpv", "--no-video", "--quiet",
                    "--http-header-fields=" + ",".join([
                        f"User-Agent: {USER_AGENT}",
                        f"Referer: {BASE_URL}/liveatc",
                    ]),
                    stream_url,
                ]
            elif player == "ffplay":
                header_str = (
                    f"User-Agent: {USER_AGENT}\r\n"
                    f"Referer: {BASE_URL}/liveatc\r\n"
                )
                cmd = [
                    "ffplay", "-nodisp", "-autoexit",
                    "-loglevel", "quiet",
                    "-headers", header_str,
                    "-i", stream_url,
                ]
            elif player == "mpg123":
                cmd = ["mpg123", "-q", stream_url]
            else:
                cmd = [player, stream_url]

            log.info("[%s] Started: %s", name, player)
            proc = subprocess.Popen(
                cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            )
            proc.wait()
            attempt = 0
            if running:
                log.warning("[%s] Stream ended, reconnecting...", name)

        except Exception as e:
            if not running:
                break
            log.error("[%s] %s", name, e)

        if not running:
            break
        delay = min(reconnect_delay * (attempt ** 0.5), 30)
        log.info("[%s] Reconnecting in %.0fs...", name, delay)
        time.sleep(delay)


def list_feeds(region=None):
    sorted_feeds = sorted(FEEDS.items(), key=lambda x: (get_region(x[0]), x[0]))
    print(f"{'ID':<22} {'Region':<10} {'Feed Name'}")
    print("-" * 75)
    for fid, fname in sorted_feeds:
        r = get_region(fid)
        if region and r.lower() != region.lower() and region.lower() not in r.lower():
            continue
        print(f"{fid:<22} {r:<10} {fname}")


def main():
    parser = argparse.ArgumentParser(
        description="LiveATC SkyListening - stream ATC audio with auto-reconnect"
    )
    parser.add_argument("feeds", nargs="*", type=str,
                        help="Feed IDs to listen to (e.g. rjtt_twr kjfk_twr)")
    parser.add_argument("--no-audio", "-n", action="store_true",
                        help="Download stream to file instead of playing")
    parser.add_argument("--output-dir", "-o", default=None,
                        help="Output directory for --no-audio")
    parser.add_argument("--reconnect-delay", "-r", type=float, default=2.0,
                        help="Base reconnection delay in seconds (default: 2)")
    parser.add_argument("--player", "-p", default=None,
                        help="Audio player: mpv, ffplay, mpg123 (default: auto-detect)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available feeds and exit")
    parser.add_argument("--region", "-rg", default=None,
                        help="Filter list by region: japan, usa, canada, europe, caribbean")

    args = parser.parse_args()

    if args.list:
        list_feeds(args.region)
        return

    if not args.feeds:
        print("Usage: python3 tacivel.py <feed_id> [feed_id2 ...]")
        print("       python3 tacivel.py --list")
        print()
        print("Example feeds:")
        print("  Japan:      rjtt_twr, rjtt_gnd, rjtt_app, rjaa_twr")
        print("  USA:        kjfk_twr, klax_twr, kord_twr, ksfo_twr")
        print("  Canada:     cyyz_twr, cyvr_twr")
        print("  All feeds:  python3 liveatc.py --list")
        sys.exit(1)

    player = args.player or detect_player()
    if not player and not args.no_audio:
        log.error("No audio player found.")
        print("Install one of:")
        print("  Linux:  sudo apt install mpv    (or ffplay | mpg123)")
        print("  macOS:  brew install mpv")
        print("  Termux: pkg install mpv")
        print("  Or use --no-audio to download streams to files instead.")
        sys.exit(1)

    feed_ids = []
    for f in args.feeds:
        if f in FEEDS:
            feed_ids.append(f)
        else:
            log.warning("Unknown feed: %s (use --list to see available feeds)", f)

    if not feed_ids:
        log.error("No valid feeds specified")
        sys.exit(1)

    log.info("Starting %d feed(s)", len(feed_ids))
    for fid in feed_ids:
        log.info("  %s -> %s", fid, FEEDS[fid])
    log.info("Press Ctrl+C to stop")

    with ThreadPoolExecutor(max_workers=len(feed_ids)) as executor:
        futures = []
        for fid in feed_ids:
            name = FEEDS[fid]
            futures.append(executor.submit(
                play_feed, fid, name,
                no_audio=args.no_audio,
                output_dir=args.output_dir,
                reconnect_delay=args.reconnect_delay,
                player=player,
            ))
        for f in futures:
            f.result()


if __name__ == "__main__":
    main()
