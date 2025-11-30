#!/bin/bash

# Load environment variables from .env (if present). .env must NOT be committed.
if [ -f ".env" ]; then
  # shellcheck disable=SC1091
  set -a
  . .env
  set +a
fi

# --- Configuration (move secrets into .env or use env vars) ---
# IMPORTANT: put sensitive values in .env or set them in your environment
# Example .env:
#   USERNAME=your_username_here
#   PASSWORD=your_password_here
URL="${URL:-https://example.com/protected/files.html}"
USERNAME="${USERNAME:-}"
PASSWORD="${PASSWORD:-}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/Users/$USER/Downloads/Automated_Web_Files}"
FILE_EXTENSIONS="${FILE_EXTENSIONS:-mp3|jpg|jpeg|png|pdf}"

if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
    echo "ERROR: USERNAME and PASSWORD are empty. Set them via environment variables or .env (don't commit credentials)."
    exit 1
fi

# Create the download directory if it doesn't exist
mkdir -p "$DOWNLOAD_DIR"

# Change to the download directory to ensure files are saved there
cd "$DOWNLOAD_DIR" || exit

echo "Starting authenticated download process..."
echo "Target URL: $URL"
echo "Download Directory: $DOWNLOAD_DIR"

# 1. Fetch the authenticated HTML content using curl
# -s: Silent mode (removes progress bar)
# -u: Basic Authentication (username:password)
# -L: Follow redirects
# We save the HTML output to a temporary file
TEMP_HTML_FILE=$(mktemp)
curl -s -u "$USERNAME:$PASSWORD" -L "$URL" > "$TEMP_HTML_FILE"

# Check if the fetch was successful (response code 200 or 300 range)
if [ $? -ne 0 ] || ! grep -q "DOCTYPE" "$TEMP_HTML_FILE"; then
    echo "ERROR: Failed to retrieve page or authentication failed."
    rm "$TEMP_HTML_FILE"
    exit 1
fi

echo "Page retrieved successfully. Parsing links..."

# 2. Extract links matching the specified extensions
# grep -oE: Find only matching parts using extended regex
# awk '...': Filters the lines to ensure they start with "http" (full URLs)
# sort -u: Removes duplicate URLs
# -P: Prints only the path/link found
LINKS=$(
    grep -oE "href=\"(http|https)://[^\"']*\.($FILE_EXTENSIONS)\"" "$TEMP_HTML_FILE" |
    awk -F'\"' '{print $2}' |
    sort -u
)

if [ -z "$LINKS" ]; then
    echo "No new files found with extensions: $FILE_EXTENSIONS"
    rm "$TEMP_HTML_FILE"
    exit 0
fi

# 3. Download the files one by one
DOWNLOAD_COUNT=0
for LINK in $LINKS; do
    FILENAME=$(basename "$LINK")
    
    # Check if the file already exists in the download directory
    if [ -f "$FILENAME" ]; then
        # This prevents re-downloading files that are already present
        echo "Skipping existing file: $FILENAME"
        continue
    fi

    echo "Downloading: $FILENAME"
    
    # Use curl again to download the specific file
    # -O: Saves file with remote filename
    # -u: Passes authentication again for the file download
    # -L: Follows redirects
    curl -s -O -u "$USERNAME:$PASSWORD" -L "$LINK"
    
    if [ $? -eq 0 ]; then
        DOWNLOAD_COUNT=$((DOWNLOAD_COUNT + 1))
    else
        echo "WARNING: Failed to download $FILENAME"
    fi
done

# Cleanup
rm "$TEMP_HTML_FILE"

echo "Process complete. $DOWNLOAD_COUNT new files downloaded to $DOWNLOAD_DIR"
