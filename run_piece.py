from content_pipeline.cli import main as pipeline_main


def main(argv=None, **kwargs):
    return pipeline_main(argv, piece=True, **kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
