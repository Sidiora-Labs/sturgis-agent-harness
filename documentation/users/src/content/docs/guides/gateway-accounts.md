---
title: "Connect provider accounts"
description: "Use device codes or browser sign-in, import account models, and recover expired or interrupted sessions."
---

The gateway can sign in directly to supported accounts. The current native choices are GitHub Copilot, Codex, Gemini CLI, and Kiro. Account credentials stay in the gateway's encrypted storage. The browser receives sign-in instructions and account metadata, not access or refresh tokens.

Use an account you are entitled to connect. A successful login does not create paid access, remove provider limits, or guarantee that every model is available. A consumer API key and a provider subscription can grant different access even when both belong to the same company.

## Pick the appropriate account flow

| Preset | Sign-in method | Account-specific setup |
| --- | --- | --- |
| GitHub Copilot | Device code | Requires Copilot access for the GitHub account you authorize. |
| Codex | Browser authorization with PKCE | Uses an eligible ChatGPT/Codex account. The default callback is `http://localhost:1455/auth/callback`. |
| Gemini CLI | Browser authorization with PKCE | Uses Google Code Assist access. The default callback is `http://localhost:8085/oauth2callback`. A Google Cloud project may be required for your account. |
| Kiro | Device code | Uses AWS Builder ID or IAM Identity Center. Optional account settings choose the AWS region and access portal. |

PKCE binds the browser authorization to a verifier retained by the gateway. You do not create or paste that verifier.

## Device-code sign-in

1. Open **Model Gateway → Account sign-in** and choose the GitHub Copilot or Kiro tile.
2. Enter a recognizable **Connection name**, then select **Continue with** the provider.
3. Copy the displayed code using **Copy code**.
4. Open the provider's sign-in link. Sign in on that provider's page and approve the displayed code.
5. Return to the gateway. It checks the pending session at the provider's allowed interval. **Check sign-in** requests a current observation; it does not override the provider's polling limits.
6. When the account is connected, the provider drawer opens. Select **Load models**, select the models you want, and choose **Connect provider**.

Account sign-in and model import are separate steps. An account listed under **Your signed-in accounts** is available for import; it does not imply an imported model connection already exists. Choose **Use ACCOUNT NAME** later to import its catalog again.

For Kiro, leave **Advanced account options** empty for provider defaults. If your organization uses another configuration, enter **AWS region** and **AWS access portal URL** before starting sign-in. Use the actual HTTPS portal URL supplied by your organization.

## Browser sign-in

1. Select the Codex or Gemini CLI tile.
2. Name the connection. For Gemini CLI, use **Advanced account options → Google Cloud project ID** only when your account requires a specific project.
3. Select **Continue with** the provider, then open its sign-in page.
4. Complete the provider's authorization. When the gateway can open the registered local callback port, the browser displays a short confirmation page. Return to the gateway; its pending window updates automatically.
5. When the account is connected, import its models in the provider drawer.

The callback address is provider-specific. Do not replace it with the model gateway's endpoint. `localhost` in a callback refers to the machine running your browser. When the gateway runs remotely, a loopback callback may therefore reach your computer rather than the gateway server.

If authorization does not return automatically, expand **Sign-in did not return automatically?**. If the callback port is already occupied or automatic return is unavailable, the gateway explains this and labels the recovery control **Complete with callback URL**. Copy the final callback URL from the browser, including `code` and `state`, into **Callback URL**, then choose **Complete sign-in**. The field is masked and sent directly to the gateway. A missing or mismatched state is rejected. Do not post this callback URL into a conversation or share it with another person.

Google account setup can include Code Assist project discovery or onboarding before models become available. Keep the sign-in window open while the account remains pending. If Google does not return an accessible project or permitted tier, correct the account/project access and start again.

## Use an existing account

Choose **Use ACCOUNT NAME** under **Your signed-in accounts**. The import drawer identifies the signed-in account and omits the API-key field. The provider endpoint is tied to that account and is not editable there. Select **Load models**, choose the import set, and connect.

Editing an existing account-backed connection reuses the attached account. Changing a connection name does not change which provider account supplies its credentials. Sign in to another account and create another connection when you want a different identity.

If that account was disconnected, or the connection came from a configuration import without credentials, **Edit** starts a new sign-in first. The resulting drawer reconnects the same connection ID. Select the intended account during sign-in, verify the imported model list, and save. This preserves routes targeting that connection's model IDs.

## Disconnect a signed-in account

1. Find the account under **Your signed-in accounts**.
2. Select its trash icon, labeled **Disconnect account ACCOUNT NAME** for assistive tools.
3. Review the confirmation. **Keep account** cancels; **Disconnect account** deletes the gateway's local sign-in credentials.

Existing connections using that account require a new sign-in. The gateway does not claim to sign you out of the provider's website or cancel your subscription. Disconnecting only an imported provider connection leaves its signed-in account available for other imports.

If the account changes while the confirmation is open, the gateway reports a conflict. Refresh account connections, review the current account, and retry. It does not silently disconnect a newer account revision.

## Cancel or recover a sign-in

**Cancel** closes a pending sign-in only after the gateway confirms its cancellation. If cancellation cannot be confirmed, the error remains visible so you can retry. An expired or denied sign-in offers **Start again**. Starting again creates a new authorization attempt; an old code or URL will not complete the new one.

| Status or error | Meaning and recovery |
| --- | --- |
| Waiting for sign-in | Finish on the provider page; the gateway polls at the advertised interval. |
| Sign-in expired | Start again and use the new code or URL. |
| Invalid callback state | Use the callback from this exact pending sign-in. If you mixed windows or sessions, start again. |
| Account mismatch | The imported preset or endpoint does not match the signed-in account. Use the original provider account. |
| Account not eligible | Check the provider's subscription, organization, project, region, and model permissions. |
| Account refreshing | Retry shortly. Another request may already be refreshing the credential. |
| Previous refresh interrupted | Sign in again. The gateway does not blindly replay a refresh token that may already have rotated. |
| Account disconnected | Sign in again and replace or reconnect the affected model connection. |

The gateway refreshes supported credentials when they approach expiry. It does not expose refresh tokens in the UI. A restart or network failure during a rotating refresh may require a fresh sign-in, because retrying a token with an unknown result could invalidate the account's remaining credentials.

## Account sign-in through another gateway

The **9router** preset connects to an existing 9router endpoint as an ordinary gateway upstream. Providers connected inside 9router remain owned by 9router; Sturgis imports the models that endpoint advertises. This is different from the native account sign-in tiles described above.

[Provider connection guide](model-gateway.md) · [Routes and app keys](gateway-routing-keys.md)
