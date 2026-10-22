# WhatsApp intake

Payers can send their proof of payment to the school's WhatsApp Business number. Each message
becomes an ordinary claim (channel `whatsapp`) and runs the same pipeline as an upload. A
WhatsApp photo is still only a claim: money is confirmed from our side (statements, alerts),
never from the picture.

## Set up (Meta WhatsApp Cloud API)

1. In the Meta developer console, add the WhatsApp product to your app and register the
   business number. Note the **phone number ID**, create a **permanent access token** (system
   user) and copy the **app secret**.
2. Set `POPS_WHATSAPP_VERIFY_TOKEN` (any random string), `POPS_WHATSAPP_APP_SECRET`,
   `POPS_WHATSAPP_ACCESS_TOKEN` and `POPS_WHATSAPP_PHONE_NUMBER_ID`. The service refuses to
   start with only some of them.
3. Configure the webhook URL as `https://<api>/whatsapp/webhook` with the same verify token,
   and subscribe to the `messages` field.

Every webhook must carry Meta's `X-Hub-Signature-256` over the body (HMAC-SHA256 with the app
secret); unsigned or forged requests get `401`.

## What payers send

| Message | What happens |
| --- | --- |
| Photo, screenshot or PDF with the payment code (e.g. `CG-H4CC093`) in the caption | A claim on that invoice. |
| The payment code as a text, then the photo within 30 minutes | The photo is joined to that code. |
| The payment SMS forwarded as text, with the code in it | A pasted-text claim (`PASTED_TEXT` signal). |
| Anything without a code | A reply asking for the code. No claim. |

Replies only acknowledge receipt ("we will confirm once the money reaches our account"). They
never say a payment is verified and never reveal fraud scores. Meta redelivers webhooks; each
message ID is handled once. Payment codes are globally unique, so one number can serve several
organisations; set `POPS_WHATSAPP_ORG_ID` to accept only one organisation's codes.

## Limits

- Each photo is its own claim. Two photos of one payment sent as separate messages become two
  claims (reviewers see the second as a duplicate reference). Ask payers to send one photo, or
  use the web form, which accepts up to 5 files per payment.
- Status updates (delivered/read) are ignored. Outcome notifications back to the payer are not
  sent yet; Cognify receives the usual `entitlement.updated` webhook.
