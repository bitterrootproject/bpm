from termcolor import colored


def debug(message: str):
    """
    Print a debug message on the screen.
    """
    print(colored("DEBUG", "blue") + ": " + message)


def info(message: str):
    """
    Print an info message on the screen.
    """
    print("INFO: " + message)


def warn(message: str):
    """
    Print a warning message on the screen.
    """
    print(colored("WARN", "yellow") + ": " + message)


def error(message: str, exiting: bool = True):
    """
    Print an error message on the screen. Optionally exit the program after printing.
    """
    print(colored("ERROR", "red") + ": " + message)

    if exiting:
        exit(1)
