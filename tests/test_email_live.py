import os
import smtplib
import ssl
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv


def test_smtp_connection():
    print("=" * 72)
    print("TEST 1 - SMTP CONNECTION TEST")
    print("=" * 72)

    load_dotenv()

    server = os.environ.get("SMTP_SERVER", "smtp.zoho.in")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")

    smtp_login_ok = False
    test_email_sent_ok = False
    issue = ""
    fix = ""

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(server, port, context=context) as smtp:
            smtp.login(user, password)
            smtp_login_ok = True
            print("SMTP LOGIN: SUCCESS")

            msg = MIMEText("This is a live SMTP test from NariNakhre test suite.")
            msg["Subject"] = "TEST: NariNakhre Email System Check"
            msg["From"] = user
            msg["To"] = "mohinicosmetics.india@gmail.com"
            smtp.sendmail(user, ["mohinicosmetics.india@gmail.com"], msg.as_string())
            test_email_sent_ok = True
            print("TEST EMAIL SENT: SUCCESS")
    except smtplib.SMTPAuthenticationError as e:
        issue = f"SMTP authentication failed: {e}"
        fix = "Check SMTP_USER and SMTP_PASS in Render environment variables"
        print(f"SMTP LOGIN FAILED: Authentication error - {e}")
        print("FIX: Check SMTP_USER and SMTP_PASS in Render environment variables")
    except Exception as e:
        issue = f"SMTP connection failed: {type(e).__name__}: {e}"
        fix = "Check SMTP host/port reachability and credentials"
        print(f"SMTP CONNECTION FAILED: {type(e).__name__}: {e}")

    return {
        "smtp_login": smtp_login_ok,
        "test_email_sent": test_email_sent_ok,
        "issue": issue,
        "fix": fix,
    }


def test_retail_contact_form():
    print("\n" + "=" * 72)
    print("TEST 2 - CONTACT FORM TRIGGER TEST")
    print("=" * 72)

    try:
        response = requests.post(
            "https://narinakhre.com/retail/contact",
            data={
                "name": "Email Test",
                "whatsapp": "9999999999",
                "email": "test@narinakhre.com",
                "message": "This is an automated email system test. Please ignore.",
            },
            allow_redirects=False,
            timeout=30,
        )

        ok = response.status_code in (200, 302)
        if ok:
            print(f"CONTACT FORM POST: SUCCESS (status {response.status_code})")
            print("CHECK RENDER LOGS: Look for 'Email sent' or 'Email send skipped'")
        else:
            print(f"CONTACT FORM POST: FAILED (status {response.status_code})")
            print(f"Response: {response.text[:500]}")
        return {"retail_contact": ok, "status_code": response.status_code}
    except Exception as e:
        print(f"CONTACT FORM POST: FAILED ({type(e).__name__}: {e})")
        return {"retail_contact": False, "status_code": None, "error": str(e)}


def test_server_email_env_vars():
    print("\n" + "=" * 72)
    print("TEST 3 - CHECK RENDER ENV VARS ARE SET")
    print("=" * 72)

    try:
        response = requests.get("https://narinakhre.com/api/email-status", timeout=30)
        if response.status_code != 200:
            print(f"EMAIL STATUS ENDPOINT: FAILED (status {response.status_code})")
            print(f"Response: {response.text[:500]}")
            return {
                "server_env_ok": False,
                "smtp_user_set": False,
                "smtp_pass_set": False,
                "status_code": response.status_code,
            }

        data = response.json()
        print("EMAIL CONFIG ON SERVER:")
        for key, value in data.items():
            print(f"  {key}: {value}")

        smtp_user_set = bool(data.get("smtp_user_set"))
        smtp_pass_set = bool(data.get("smtp_pass_set"))

        if not smtp_user_set:
            print("FIX NEEDED: SMTP_USER not set in Render environment variables")
            print("Go to: Render -> narinakhre-production -> Environment")
            print("Add: SMTP_USER = info@narinakhre.com")

        if not smtp_pass_set:
            print("FIX NEEDED: SMTP_PASS not set in Render environment variables")
            print("Add: SMTP_PASS = <your zoho password>")

        return {
            "server_env_ok": smtp_user_set and smtp_pass_set,
            "smtp_user_set": smtp_user_set,
            "smtp_pass_set": smtp_pass_set,
            "status_code": response.status_code,
        }
    except Exception as e:
        print(f"EMAIL STATUS ENDPOINT: FAILED ({type(e).__name__}: {e})")
        return {
            "server_env_ok": False,
            "smtp_user_set": False,
            "smtp_pass_set": False,
            "status_code": None,
            "error": str(e),
        }


def test_wholesale_contact_form():
    print("\n" + "=" * 72)
    print("TEST 4 - WHOLESALE CONTACT FORM")
    print("=" * 72)

    try:
        response = requests.post(
            "https://wholesale.narinakhre.com/contact",
            data={
                "name": "Email Test Wholesale",
                "whatsapp": "9999999999",
                "email": "test@narinakhre.com",
                "message": "Automated wholesale email test. Please ignore.",
                "system_verification_token": "",
            },
            allow_redirects=False,
            timeout=30,
        )

        ok = response.status_code in (200, 302)
        if ok:
            print(f"WHOLESALE CONTACT FORM: SUCCESS (status {response.status_code})")
            print("CHECK RENDER LOGS: Look for wholesale email send result")
        else:
            print(f"WHOLESALE CONTACT FORM: FAILED (status {response.status_code})")
            print(f"Response: {response.text[:500]}")
        return {"wholesale_contact": ok, "status_code": response.status_code}
    except Exception as e:
        print(f"WHOLESALE CONTACT FORM: FAILED ({type(e).__name__}: {e})")
        return {"wholesale_contact": False, "status_code": None, "error": str(e)}


def write_summary(path, results):
    smtp_login = results["smtp"]["smtp_login"]
    email_sent = results["smtp"]["test_email_sent"]
    retail_ok = results["retail"]["retail_contact"]
    wholesale_ok = results["wholesale"]["wholesale_contact"]
    env_ok = results["env"]["server_env_ok"]

    failures = []
    fixes = []

    if not smtp_login:
        failures.append(results["smtp"].get("issue") or "SMTP login failed")
        fixes.append(results["smtp"].get("fix") or "Fix SMTP credentials on server")
    elif not email_sent:
        failures.append("SMTP login worked but test email was not sent")
        fixes.append("Verify sender permissions and recipient acceptance in Zoho")

    if not retail_ok:
        failures.append("Retail contact form call failed or returned unexpected status")
        fixes.append("Check retail route health and email code path on production")

    if not wholesale_ok:
        failures.append("Wholesale contact form call failed or returned unexpected status")
        fixes.append("Check wholesale endpoint DNS, route, and email path")

    if not env_ok:
        failures.append("Production SMTP env vars are missing or unreadable")
        fixes.append("Set SMTP_USER and SMTP_PASS in Render environment")

    root_cause = "; ".join(failures) if failures else "No failures observed in automated checks"
    fix_required = "; ".join(dict.fromkeys(fixes)) if fixes else "No immediate fix required"

    lines = [
        "# Live Email Test Summary",
        "",
        f"- SMTP connection: {'PASS' if smtp_login else 'FAIL'}",
        f"- Test email sent: {'PASS' if email_sent else 'FAIL'}",
        f"- Contact form retail: {'PASS' if retail_ok else 'FAIL'}",
        f"- Contact form wholesale: {'PASS' if wholesale_ok else 'FAIL'}",
        f"- Server env vars configured: {'PASS' if env_ok else 'FAIL'}",
        f"- Root cause if any failures: {root_cause}",
        f"- Fix required: {fix_required}",
        "",
    ]

    with open(path, "w", encoding="utf-8") as summary_file:
        summary_file.write("\n".join(lines))


def main():
    os.makedirs("reports", exist_ok=True)

    smtp_results = test_smtp_connection()
    retail_results = test_retail_contact_form()
    env_results = test_server_email_env_vars()
    wholesale_results = test_wholesale_contact_form()

    all_results = {
        "smtp": smtp_results,
        "retail": retail_results,
        "env": env_results,
        "wholesale": wholesale_results,
    }

    write_summary("reports/email_test_summary.md", all_results)

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"SMTP connection: {'PASS' if smtp_results['smtp_login'] else 'FAIL'}")
    print(f"Test email sent: {'PASS' if smtp_results['test_email_sent'] else 'FAIL'}")
    print(f"Contact form retail: {'PASS' if retail_results['retail_contact'] else 'FAIL'}")
    print(f"Contact form wholesale: {'PASS' if wholesale_results['wholesale_contact'] else 'FAIL'}")
    print(f"Server env vars configured: {'PASS' if env_results['server_env_ok'] else 'FAIL'}")
    print("Summary written to reports/email_test_summary.md")


if __name__ == "__main__":
    main()
