#!/usr/bin/env -S awk -f

# Print the header line
/Type[[:space:]]+Name\/Value/ {
    sub(/^[[:space:]]+/, "")
    sub(/^[^[:space:]]+[[:space:]]+/, "")
    print
    start=1
    next
}

# Process and print all subsequent lines
start == 1 {
    # Remove leading whitespace
    sub(/^[[:space:]]+/, "")

    # Remove the first column (Tag/hex)
    sub(/^[^[:space:]]+[[:space:]]+/, "")

    # Print the result
    if ($0 ~ /RUNPATH/ || $0 ~ /RPATH/ || $0 ~ /NEEDED/) {
        print
    }
}
