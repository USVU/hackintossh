#!/usr/bin/env python3
import argparse
import logging
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scanner")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=2))
SESSION.mount("http://", requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=2))

running = True

FEEDS = {
    4142: "Springfield (MO) Police and Fire, Greene County Sheriff",
    21738: "Pittsburgh Police, Fire and EMS",
    5688: "Sacramento County Sheriff and City Police",
    19917: "Carroll County (MD) Fire and EMS",
    11446: "Cleveland Police and Metro Housing Authority",
    41336: "Chemung County (NY) Police and Fire",
    5725: "Des Moines Police Dispatch 1",
    5974: "Richland County (OH) Public Safety",
    6007: "Jefferson County (NY) Police, Fire and EMS",
    9119: "Bradley County (TN) Sheriff, Cleveland Police",
    9809: "Frederick County (MD) Sheriff, Fire and EMS",
    10911: "Eastern Upper Peninsula (MI) Public Safety",
    11208: "Columbus Police Dispatch - Citywide",
    13671: "Detroit Police Dispatch",
    13928: "Marion County (OH) Public Safety",
    14395: "Lincoln (NE) Police and Fire, Lancaster County Sheriff",
    16120: "Dorchester County (SC) Fire and EMS",
    16904: "Outagamie County (WI) Public Safety",
    188: "Allen County (OH) Public Safety",
    20884: "Schuylkill County (PA) Fire",
    21385: "Isabella County (MI) Police and Fire",
    21905: "Elkhart County (IN) Police, Fire and EMS",
    22101: "Franklin County (PA) Fire and EMS Alerts",
    23096: "Monroe County (MI) Sheriff, Police, Fire and EMS",
    24330: "Downriver (MI) Public Safety",
    24391: "Brunswick County (NC) Sheriff, Fire and EMS",
    24798: "Muskingum County (OH) Sheriff, Fire and EMS",
    25304: "Terre Haute (IN) Public Safety",
    26366: "Sandusky County (OH) Sheriff, Fire and EMS",
    26933: "Buffalo (NY) Police, Fire and EMS",
    30589: "Jackson County (MI) Public Safety",
    31838: "Rutherford County (NC) Sheriff, Fire and EMS",
    32252: "Toledo (OH) Police Central Dispatch",
    32602: "Indianapolis Metropolitan Police",
    32623: "Temiskaming District (ON) Police, Fire and EMS",
    33371: "Clear Creek County (CO) Law Enforcement, Fire, EMS",
    35154: "Rockingham County (NC) Public Safety",
    38096: "Fayette County (PA) Public Safety Dispatch",
    38965: "Rock County (WI) Public Safety",
    38966: "South Bend (IN) Public Safety",
    40688: "Mesa County (CO) and Grand Junction Police, Fire and EMS",
    41473: "Onondaga County (NY) Public Safety",
    41475: "Oswego County (NY) Public Safety",
    41557: "Greater Lansing Area (MI) Public Safety",
    43441: "Armstrong County (PA) Public Safety",
    1005: "Tioga County (PA) Police, Fire and EMS",
    1656: "Juniata County (PA) Fire and EMS",
    3178: "Essex County (NY) Police, Fire, and EMS",
    3737: "Westmoreland County (PA) Public Safety",
    5266: "Ionia County (MI) Sheriff, Fire and EMS",
    41210: "Chicago Police Citywide Dispatch",
    32889: "Houston Police All Districts",
    12145: "Phoenix Police",
    4603: "Philadelphia Police Citywide",
    5318: "Dallas Police Central Dispatch",
    43758: "Austin Combined Law / Fire / EMS",
    215: "Memphis Police and Shelby County Sheriff",
    40593: "Baltimore City Police",
    9840: "Milwaukee County Law Enforcement",
    40168: "Seattle Downtown Law Enforcement",
    45596: "Miami Police Central Dispatch",
    40184: "NYPD Citywide 1 (Manhattan/Brooklyn)",
    1189: "NYPD Bronx/Manhattan Transit",
    26569: "LAPD Dispatch - Valley Bureau",
    2846: "Los Angeles City Fire",
    13549: "Fresno City Police, Fire and EMS",
    318: "Oklahoma City Area Police and Fire",
    20766: "Portland (OR) Area Fire and Rescue",
    26400: "Las Vegas and Clark County Fire",
    13058: "Charlotte and Mecklenburg County Fire",
    41252: "Jacksonville Fire Rescue",
    46343: "Boston Fire Department",
    36636: "Boston EMS",
    42114: "Tennessee Highway Patrol - Nashville",
    42597: "San Antonio Fire",
    10294: "San Diego City and Poway Fire",
    35626: "Atlanta Fire Rescue",
}


def signal_handler(sig, frame):
    global running
    log.info("Shutting down...")
    running = False
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def sanitize_filename(s):
    return re.sub(r'[^\w.-]+', '_', s).strip('_')


def extract_stream_url(feed_id):
    url = f"https://www.broadcastify.com/listen/feed/{feed_id}"
    resp = SESSION.get(url, timeout=15)
    resp.raise_for_status()
    if f"/feed/{feed_id}" not in resp.url:
        raise ValueError(f"Feed {feed_id} redirected to browse page")
    m = re.search(r'relayUrl:\s*"([^"]+)"', resp.text)
    if m and m.group(1):
        return m.group(1).replace("\\/", "/")
    m = re.search(r'hlsUrl:\s*"([^"]+)"', resp.text)
    if m:
        return m.group(1).replace("\\/", "/")
    raise ValueError(f"Could not extract stream URL for feed {feed_id}")


def is_termux():
    return "com.termux" in os.environ.get("PREFIX", "") or shutil.which("termux-setup-package") is not None


def detect_player():
    if shutil.which("ffmpeg") and shutil.which("paplay"):
        return "ffmpeg-paplay"
    if shutil.which("ffmpeg") and shutil.which("aplay"):
        return "ffmpeg-aplay"
    if shutil.which("mpv"):
        return "mpv"
    if not is_termux() and shutil.which("ffplay"):
        return "ffplay"
    if shutil.which("mpg123"):
        return "mpg123"
    return None


def get_headers(feed_id):
    return (
        f"User-Agent: {USER_AGENT}\r\n"
        f"Referer: https://www.broadcastify.com/listen/feed/{feed_id}\r\n"
    )


def play_feed(feed_id, name, no_audio=False, output_dir=None, reconnect_delay=2, player=None):
    global running
    attempt = 0

    while running:
        attempt += 1
        try:
            stream_url = extract_stream_url(feed_id)
            log.info("[%s] Connecting... (attempt %d)", name, attempt)
            safe_name = sanitize_filename(name)
            headers = get_headers(feed_id)

            if no_audio:
                out_dir = output_dir or "."
                os.makedirs(out_dir, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                out_path = os.path.join(out_dir, f"{feed_id}_{safe_name}_{ts}.mp3")
                log.info("[%s] Saving to %s", name, out_path)
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-headers", headers,
                    "-i", stream_url,
                    "-map", "0:0", "-c", "copy",
                    out_path,
                ]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait()
                if running:
                    log.warning("[%s] Stream ended, reconnecting...", name)
                continue

            if player == "ffmpeg-paplay":
                ffmpeg = subprocess.Popen([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-headers", headers,
                    "-i", stream_url,
                    "-map", "0:0",
                    "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    "-",
                ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                player_proc = subprocess.Popen(
                    ["paplay", "--raw", "--format=s16le", "--rate=16000", "--channels=1"],
                    stdin=ffmpeg.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                ffmpeg.stdout.close()
                log.info("[%s] Started: ffmpeg + paplay", name)
                player_proc.wait()
                ffmpeg.wait()

            elif player == "ffmpeg-aplay":
                ffmpeg = subprocess.Popen([
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-headers", headers,
                    "-i", stream_url,
                    "-map", "0:0",
                    "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    "-",
                ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                player_proc = subprocess.Popen(
                    ["aplay", "--raw", "--format=S16_LE", "--rate=16000", "--channels=1"],
                    stdin=ffmpeg.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                ffmpeg.stdout.close()
                log.info("[%s] Started: ffmpeg + aplay", name)
                player_proc.wait()
                ffmpeg.wait()

            elif player == "mpv":
                cmd = [
                    "mpv", "--no-video", "--quiet",
                    "--http-header-fields=" + ",".join([
                        f"User-Agent: {USER_AGENT}",
                        f"Referer: https://www.broadcastify.com/listen/feed/{feed_id}",
                    ]),
                    stream_url,
                ]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait()

            elif player == "ffplay":
                cmd = [
                    "ffplay", "-nodisp", "-autoexit",
                    "-loglevel", "quiet",
                    "-headers", headers,
                    "-i", stream_url,
                ]
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait()

            elif player == "mpg123":
                subprocess.Popen(["mpg123", "-q", stream_url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait()

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


def get_listener_count(feed_id):
    try:
        url = f"https://www.broadcastify.com/listen/feed/{feed_id}"
        resp = SESSION.get(url, timeout=10)
        m = re.search(r'id="lp-hero-listeners">(\d+)', resp.text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def list_feeds():
    print(f"{'ID':>6}  {'Lstn':>5}  {'Feed Name'}")
    print("-" * 75)
    counts = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        fut_map = {ex.submit(get_listener_count, fid): fid for fid in FEEDS}
        for f in concurrent.futures.as_completed(fut_map):
            fid = fut_map[f]
            counts[fid] = f.result()
    for fid, fname in sorted(FEEDS.items()):
        c = counts.get(fid)
        cnt = f"{c:>4d}" if c is not None else "   ?"
        print(f"{fid:>6}  {cnt:>5}  {fname}")


def main():
    parser = argparse.ArgumentParser(description="Broadcastify Police Scanner")
    parser.add_argument("feeds", nargs="*", type=int, help="Feed IDs to listen to")
    parser.add_argument("--no-audio", "-n", action="store_true", help="Download stream to file")
    parser.add_argument("--output-dir", "-o", default=None, help="Output directory for --no-audio")
    parser.add_argument("--reconnect-delay", "-r", type=float, default=2.0, help="Base reconnect delay (default: 2)")
    parser.add_argument("--player", "-p", default=None, help="Player: ffmpeg, ffplay, mpv, mpg123")
    parser.add_argument("--list", "-l", action="store_true", help="List available feeds")

    args = parser.parse_args()

    if args.list:
        list_feeds()
        return

    if not args.feeds:
        print("Usage: python3 narcens.py <feed_id> [feed_id2 ...]")
        print("       python3 narcens.py --list")
        sys.exit(1)

    log.info("Starting %d feed(s)", len(args.feeds))
    for fid in args.feeds:
        name = FEEDS.get(fid, f"Custom feed {fid}")
        log.info("  %s -> %s", fid, name)
    log.info("Press Ctrl+C to stop")

    player = args.player or detect_player()
    if not player:
        log.error("No audio player found. Install ffmpeg+paplay, mpv, or ffplay.")
        sys.exit(1)

    with ThreadPoolExecutor(max_workers=len(args.feeds)) as executor:
        futures = []
        for fid in args.feeds:
            name = FEEDS.get(fid, str(fid))
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
