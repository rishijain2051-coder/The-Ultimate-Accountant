"""The office: this PC as the office server or an office client, signed in with its own keys
to the control plane (control-plane/, PLAN-office.md).

Like the Keeper, nothing here may import the Invoice Bridge (app.bridge) or Assurance Console
(app.backend); app/keeper/tests/test_kept_apart.py holds it. In "alone" mode, the default,
none of it does anything: the app is what it was.
"""
