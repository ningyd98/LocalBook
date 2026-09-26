# Bonjour Service Discovery - Manual Testing Guide

## Prerequisites

1. Install the zeroconf library:
   ```bash
   cd /path/to/LocalBook
   uv pip install zeroconf
   ```

## Testing Steps

### 1. Start the Bonjour Service

```bash
cd /path/to/LocalBook
python3 server/discovery/manual_test_bonjour.py
```

The service will:
- Start advertising on port 3780
- Run for 30 seconds
- Display status messages

### 2. Verify Discovery (in another terminal)

Use macOS's built-in `dns-sd` tool to browse for LocalBook services:

```bash
dns-sd -B _localbook._tcp
```

Expected output:
```
Browsing for _localbook._tcp
DATE: ---Tue 12 Sep 2024---
 9:00:00.000  ...STARTING...
Timestamp     A/R    Flags  if Domain               Service Type         Instance Name
 9:00:01.000  Add        2   4 local.               _localbook._tcp.     LocalBook on <hostname>
```

### 3. Resolve Service Details

To see the full service information (port, IP, TXT records):

```bash
dns-sd -L "LocalBook on <hostname>" _localbook._tcp
```

Replace `<hostname>` with your actual hostname from step 2.

Expected TXT records:
- `version=1.3.0`
- `reader_api=v1`
- `port=3780`

### 4. Stop the Service

Press Ctrl+C in the terminal running the service, or wait 30 seconds for automatic stop.

## Unit Tests

Run the unit tests (mocked, no network required):

```bash
cd /path/to/LocalBook
python3 -m pytest server/discovery/tests/test_bonjour.py -v
```

Note: The integration test (`test_real_service_lifecycle`) is skipped by default.

## Troubleshooting

### "No module named 'zeroconf'"
Install the library: `uv pip install zeroconf`

### Service not appearing in dns-sd
1. Check firewall settings
2. Ensure you're on the same network
3. Verify the service started without errors
4. Try restarting Bonjour/mDNS daemon: `sudo launchctl stop com.apple.mDNSResponder && sudo launchctl start com.apple.mDNSResponder`

### Permission errors
The service needs network access. Run with appropriate permissions or check system firewall settings.
