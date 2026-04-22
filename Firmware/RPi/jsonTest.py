
def trim_log_file(filepath, max_lines=42):
    try:
        with open(filepath, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return  # Nothing to trim

    # Keep only last max_lines
    lines = lines[-max_lines:]

    # Overwrite file
    with open(filepath, "w") as f:
        f.writelines(lines)



trim_log_file("/home/gedonsoft/Weather/capture.log")
