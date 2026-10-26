def _init():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import tuimux
    except ImportError:
        exit()

    tuimux.init_gdb()


_init()
del _init
