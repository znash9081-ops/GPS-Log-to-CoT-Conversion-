# GPS CSV to Cursor on Target (CoT) Generator

This Python script monitors multiple CSV files containing GPS tracking data and broadcasts them as **Cursor on Target (CoT)** XML messages via UDP. It supports both historical track playback and a real-time "jump" mode, making it ideal for replaying mission data into platforms like ATAK, WinTAK, or VBS3.

## Key Features

* **Multi-File Concurrent Processing:** Uses threading to monitor and process multiple CSV files (e.g., `targets.csv`, `targets1.csv`) simultaneously.
* **Flexible Header Mapping:** Automatically identifies data columns (Latitude, Longitude, Altitude, Speed, etc.) using a wide range of common aliases.
* **Dynamic Command Interface:** A built-in command listener allows you to interact with the simulation while it is running.
* **Automatic Cleanup:** Sends "tombstone" (removal) messages for old coordinates before broadcasting new positions to prevent ghosting on the end-user map.
* **Configurable Playback:** Supports adjusting the polling interval and fast-forwarding to the latest data row in real-time.

## Commands

While the script is running, you can enter the following commands into the console:

* `jump`: Fast-forwards all tracked files to their latest available row, switching from historical playback to real-time mode.
* `speed <seconds>`: Changes the polling interval (e.g., `speed 1.5`) to speed up or slow down the broadcast.
* `quit`: Safely terminates the program and all background threads.

## Configuration

The script includes a configuration section at the top of the file to define:
* **`TARGET_IP` & `TARGET_PORT`**: The destination address (default is `127.0.0.1:4242`).
* **`CSV_FILES`**: A list of file paths to monitor.
* **`POLLING_INTERVAL_SECONDS`**: How often the script checks for new data (default is 5.0 seconds).
* **`FIXED_CALLSIGN_BASE`**: The default name used if no callsign is found in the CSV data.

## Technical Details

* **Transport**: UDP Unicast.
* **CoT Type**: Standard Ground Track (`a-h-G-i-I`) and Removal Type (`t-x-c-c`).
* **Requirements**: 
    * Python 3.x.
    * Standard libraries: `csv`, `socket`, `threading`, `time`.

## Usage

1.  Place your tracking CSVs in the same directory as the script or update the `CSV_FILES` list.
2.  Run the script:
    ```bash
    python GPS_CSV_to_CoT.py
    ```
3.  (Optional) Specify a custom starting speed as a command-line argument:
    ```bash
    python GPS_CSV_to_CoT.py 1.0
    ```
