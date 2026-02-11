import csv
import socket
import time
from datetime import datetime, timezone, timedelta
import os
import threading
import sys

# Configuration
TARGET_IP = "127.0.0.1"
TARGET_PORT = 4242
CSV_FILES = ["targets.csv", "targets1.csv", "targets2.csv"]
POLLING_INTERVAL_SECONDS = 5.0
JUMP_TO_LATEST_ROW_ENABLED = False
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
COT_TYPE = "a-h-G-i-I"
COT_REMOVAL_TYPE = "t-x-c-c"
STALE_DELTA_SECONDS = 300
FIXED_CALLSIGN_BASE = "TMIT"
KNOTS_TO_M_PER_SEC = 0.514444

POTENTIAL_LAT_HEADERS = ["Latitude (DD)", "Latitude", "lat", "LAT"]
POTENTIAL_LON_HEADERS = ["Longitude (DD)", "Longitude", "lon", "LON"]
POTENTIAL_ALT_HEADERS = ["Altitude (m)", "Altitude", "alt", "hae", "hAE"]
POTENTIAL_SPEED_HEADERS = ["Speed (knots)", "Speed", "speed_knots", "knots"]
POTENTIAL_BEARING_HEADERS = ["Bearing (deg)", "Bearing", "heading", "hdg"]
POTENTIAL_CALLSIGN_HEADERS = ["callsign", "Callsign", "Unit_ID", "ID", "Name", "UID"]
POTENTIAL_TIMESTAMP_HEADERS = ["timestamp (utc)", "timestamp", "Time (UTC)", "UTC", "DATETIME"]

FILE_STATE = {}
STATE_LOCK = threading.Lock()
JUMP_COMMAND_PENDING = False


def find_column_header(available_headers, potential_list):
    available_headers_lower = {h.lower(): h for h in available_headers}
    for potential in potential_list:
        if potential.lower() in available_headers_lower:
            return available_headers_lower[potential.lower()]
    return None


def generate_cot_xml(track_data, keys, fixed_callsign_default, cot_type):
    callsign = track_data.get(keys.get('callsign'))
    if not callsign or str(callsign).strip() == "":
        callsign = fixed_callsign_default

    now = datetime.now(timezone.utc)
    start_time = now.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    uid = callsign.replace(" ", "_").replace(".", "_")

    if cot_type == COT_REMOVAL_TYPE:
        stale_time = (now + timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        cot_xml = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<event version='2.0' uid='{uid}' type='{COT_REMOVAL_TYPE}' how='h-e' time='{start_time}' start='{start_time}' stale='{stale_time}'>
  <point lat='0.0' lon='0.0' hae='0.0' ce='9999999.0' le='9999999.0'/>
  <detail>
    <link uid='{uid}' type='{COT_TYPE}' relation='t-d'/>
  </detail>
</event>"""
        return cot_xml

    try:
        lat_val = track_data.get(keys['lat'])
        lon_val = track_data.get(keys['lon'])
        if lat_val is None or lon_val is None:
            raise KeyError(f"Missing latitude ({keys['lat']}) or longitude ({keys['lon']}) in row.")
        lat = float(lat_val)
        lon = float(lon_val)
        alt_val = track_data.get(keys.get('alt'))
        alt = float(alt_val) if alt_val else 0.0
        speed_knots_val = track_data.get(keys.get('speed'))
        speed_knots = float(speed_knots_val) if speed_knots_val else 0.0
        speed_ms = speed_knots * KNOTS_TO_M_PER_SEC
        bearing_deg_val = track_data.get(keys.get('bearing'))
        bearing_deg = float(bearing_deg_val) if bearing_deg_val else 0.0
    except (ValueError, KeyError) as e:
        print(f"[ERROR] Invalid numerical value or missing key in row: {e}")
        return None

    stale_time = (now + timedelta(seconds=STALE_DELTA_SECONDS)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    LE = 10.0

    cot_xml = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<event version='2.0' uid='{uid}' type='{COT_TYPE}' how='m-g' time='{start_time}' start='{start_time}' stale='{stale_time}'>
  <point lat='{lat}' lon='{lon}' hae='{alt}' ce='{LE}' le='{LE}'/>
  <detail>
    <uid DUID='{uid}'/>
    <contact callsign='{callsign}'/>
    <track course='{bearing_deg}' speed='{speed_ms}'/>
  
    </detail>
</event>"""

    return cot_xml


def send_cot(cot_xml, ip, port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(cot_xml.encode('utf-8'), (ip, port))
        sock.close()
        return True
    except socket.error as e:
        print(f"[ERROR] Failed to send COT to {ip}:{port}. Socket error: {e}")
        return False


def command_listener():
    global JUMP_COMMAND_PENDING, POLLING_INTERVAL_SECONDS
    print('[INFO] Command listener ready. Enter: jump | quit | speed <seconds>')
    while True:
        try:
            command_input = sys.stdin.readline().strip().lower()
            parts = command_input.split()
            command = parts[0] if parts else ""
            if command == 'jump':
                with STATE_LOCK:
                    JUMP_COMMAND_PENDING = True
                print('[INFO] jump received')
            elif command == 'quit':
                print('[INFO] quit received. Exiting')
                os._exit(0)
            elif command == 'speed':
                if len(parts) < 2:
                    print(f"[INFO] speed requires an interval in seconds. Current: {POLLING_INTERVAL_SECONDS}s")
                    continue
                try:
                    new_interval = float(parts[1])
                    if new_interval <= 0:
                        raise ValueError('Interval must be positive')
                    with STATE_LOCK:
                        POLLING_INTERVAL_SECONDS = new_interval
                    print(f"[INFO] Polling interval set to: {POLLING_INTERVAL_SECONDS} seconds")
                except ValueError:
                    print(f"[ERROR] Invalid speed value: '{parts[1]}'")
            elif command:
                print(f"[INFO] Unknown command: '{command_input}'")
        except EOFError:
            break
        except Exception as e:
            print(f"[ERROR] Command listener: {e}")


def process_csv_continuously(csv_filepath, target_ip, target_port):
    global JUMP_COMMAND_PENDING, JUMP_TO_LATEST_ROW_ENABLED
    with STATE_LOCK:
        if csv_filepath not in FILE_STATE:
            FILE_STATE[csv_filepath] = {
                'last_line': 0,
                'previous_row_data': None,
                'discovered_keys': None,
                'fieldnames': None,
                'header_processed': False
            }
        state = FILE_STATE[csv_filepath]

    try:
        file_index = CSV_FILES.index(csv_filepath)
        unique_callsign_default = f"{FIXED_CALLSIGN_BASE}{file_index + 1}"
    except ValueError:
        unique_callsign_default = FIXED_CALLSIGN_BASE

    try:
        with open(csv_filepath, mode='r', newline='', encoding='utf-8') as file:
            lines = file.readlines()
            total_lines = len(lines)

            if total_lines > 0 and not state['header_processed']:
                header_line = lines[0].strip()
                if header_line.startswith('"') and header_line.endswith('"'):
                    header_line = header_line[1:-1]
                fieldnames = [h.strip() for h in header_line.split(',')]
                discovered_keys = {}
                discovered_keys['lat'] = find_column_header(fieldnames, POTENTIAL_LAT_HEADERS)
                discovered_keys['lon'] = find_column_header(fieldnames, POTENTIAL_LON_HEADERS)
                discovered_keys['callsign'] = find_column_header(fieldnames, POTENTIAL_CALLSIGN_HEADERS)
                discovered_keys['alt'] = find_column_header(fieldnames, POTENTIAL_ALT_HEADERS)
                discovered_keys['speed'] = find_column_header(fieldnames, POTENTIAL_SPEED_HEADERS)
                discovered_keys['bearing'] = find_column_header(fieldnames, POTENTIAL_BEARING_HEADERS)
                discovered_keys['timestamp'] = find_column_header(fieldnames, POTENTIAL_TIMESTAMP_HEADERS)
                if not discovered_keys.get('lat') or not discovered_keys.get('lon'):
                    print(f"[ERROR] File '{csv_filepath}': Required LAT/LON headers not found. Skipping.")
                    state['last_line'] = total_lines
                    return
                with STATE_LOCK:
                    state['discovered_keys'] = discovered_keys
                    state['fieldnames'] = fieldnames
                    state['header_processed'] = True
                print(f"[INFO] Initialized header for '{csv_filepath}' (Default Callsign: {unique_callsign_default})")

            line_to_process_index = max(1, state['last_line'])

            if JUMP_COMMAND_PENDING and line_to_process_index < total_lines:
                with STATE_LOCK:
                    line_to_process_index = max(1, total_lines - 1)
                    state['last_line'] = line_to_process_index
                    JUMP_TO_LATEST_ROW_ENABLED = True
                print(f"[INFO] JUMP executed for '{csv_filepath}'. Starting at line {line_to_process_index + 1}.")

            line_to_process_index = max(1, state['last_line'])

            if line_to_process_index >= total_lines:
                if JUMP_TO_LATEST_ROW_ENABLED and total_lines > 1:
                    line_index = total_lines - 1
                else:
                    return
            else:
                line_index = line_to_process_index

            line = lines[line_index]

            previous_row_data = state['previous_row_data']
            discovered_keys = state['discovered_keys']
            fieldnames = state['fieldnames']

            row_string = line.strip()
            if row_string.startswith('"') and row_string.endswith('"'):
                row_string = row_string[1:-1]

            try:
                row_reader = csv.reader([row_string])
                values = next(row_reader)
            except Exception:
                values = [v.strip() for v in row_string.split(',')]

            if len(values) != len(fieldnames):
                print(f"[WARNING] Row {line_index + 1} in '{csv_filepath}' has {len(values)} columns, expected {len(fieldnames)}. Skipping.")
                return

            row = dict(zip(fieldnames, values))
            print(f"[INFO] Processing '{csv_filepath}' (Line {line_index + 1})")

            if previous_row_data:
                removal_cot = generate_cot_xml(previous_row_data, discovered_keys, unique_callsign_default, COT_REMOVAL_TYPE)
                if removal_cot:
                    callsign = previous_row_data.get(discovered_keys.get('callsign'), unique_callsign_default)
                    print(f"[INFO] Sending removal for '{callsign}'")
                    send_cot(removal_cot, target_ip, target_port)
                    time.sleep(1)

            current_callsign = row.get(discovered_keys.get('callsign'), unique_callsign_default)
            print(f"[INFO] Processing new coordinate for '{current_callsign}'")
            cot_xml = generate_cot_xml(row, discovered_keys, unique_callsign_default, COT_TYPE)

            if cot_xml:
                send_cot(cot_xml, target_ip, target_port)
                with STATE_LOCK:
                    state['previous_row_data'] = row
                    if not JUMP_TO_LATEST_ROW_ENABLED:
                        state['last_line'] = line_index + 1
                    elif line_index < total_lines - 1:
                        state['last_line'] = line_index + 1
                    else:
                        state['last_line'] = line_index
                print(f"[INFO] Sent new position for '{csv_filepath}'.")
            else:
                with STATE_LOCK:
                    state['previous_row_data'] = None

    except FileNotFoundError:
        print(f"[ERROR] File '{csv_filepath}' not found. Skipping.")
    except Exception as e:
        print(f"[ERROR] Processing '{csv_filepath}': {e}")


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            initial_interval = float(sys.argv[1])
            if initial_interval <= 0:
                raise ValueError("Interval must be positive")
            POLLING_INTERVAL_SECONDS = initial_interval
            print(f"[INFO] Using command-line initial interval: {POLLING_INTERVAL_SECONDS} seconds.")
    except Exception as e:
        print(f"[WARNING] Error processing command-line argument: {e}. Using default {POLLING_INTERVAL_SECONDS} seconds.")

    print(f"--- GPS CSV to CoT started for {len(CSV_FILES)} file(s) ---")
    print(f"Target: {TARGET_IP}:{TARGET_PORT}")
    print(f"Polling interval: {POLLING_INTERVAL_SECONDS} seconds.")
    print(f"Default Callsign Base: {FIXED_CALLSIGN_BASE}")

    listener_thread = threading.Thread(target=command_listener, daemon=True)
    listener_thread.start()
    time.sleep(0.5)

    print('[INFO] Historical playback active. Use "jump" to switch to latest rows.')

    while True:
        threads = []
        for csv_file in CSV_FILES:
            thread = threading.Thread(target=process_csv_continuously, args=(csv_file, TARGET_IP, TARGET_PORT))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()
        if JUMP_COMMAND_PENDING:
            with STATE_LOCK:
                JUMP_COMMAND_PENDING = False
                print('[INFO] Command jump processed. Tracks in real-time mode.')
        current_interval = POLLING_INTERVAL_SECONDS
        time.sleep(current_interval)