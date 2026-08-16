from services.active_pipeline_cleaner import loop

if __name__ == "__main__":
    loop(cutoff_seconds=600, interval=600)
