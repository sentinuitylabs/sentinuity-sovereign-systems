from services.active_pipeline_cleaner import clean_once

def launch_nuke(cutoff_seconds=600, dry_run=False):
    return clean_once(cutoff_seconds=cutoff_seconds, dry_run=dry_run, backup=True if not dry_run else False)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    launch_nuke(cutoff_seconds=args.cutoff, dry_run=args.dry_run)
