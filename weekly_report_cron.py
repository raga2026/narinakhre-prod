"""
Entry point for the Render Cron Job service defined in render.yaml. Runs
once a week, does nothing but POST to the main app's protected
/cron/weekly-report endpoint -- all the actual report logic (DB queries,
email rendering/sending) lives in app.py's send_weekly_report_email(), so
there's exactly one place that can drift from the admin dashboard's numbers.

Required env vars (set on the cron service in Render, not the web service):
    WEEKLY_REPORT_URL          e.g. https://narinakhre.com/cron/weekly-report
    WEEKLY_REPORT_CRON_SECRET  must match the same-named var on the web service
"""
import os
import sys

import requests


def main():
    url = os.environ.get('WEEKLY_REPORT_URL', '')
    secret = os.environ.get('WEEKLY_REPORT_CRON_SECRET', '')
    if not url or not secret:
        print('WEEKLY_REPORT_URL and WEEKLY_REPORT_CRON_SECRET must both be set', file=sys.stderr)
        sys.exit(1)

    resp = requests.post(url, headers={'X-Cron-Secret': secret}, timeout=30)
    print(f'{resp.status_code}: {resp.text}')
    if resp.status_code != 200:
        sys.exit(1)


if __name__ == '__main__':
    main()
