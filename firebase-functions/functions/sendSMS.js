/**
 * Firebase Cloud Function for sending SMS via Twilio
 *
 * This function integrates Firebase with Twilio for SMS delivery.
 * Deploy this to Firebase Cloud Functions and configure the URL in your .env file.
 *
 * Setup:
 * 1. npm install firebase-functions firebase-admin twilio
 * 2. firebase init functions
 * 3. Add Twilio credentials to Firebase Secret Manager:
 *    firebase functions:secrets:set TWILIO_ACCOUNT_SID
 *    firebase functions:secrets:set TWILIO_AUTH_TOKEN
 *    firebase functions:secrets:set TWILIO_PHONE_NUMBER
 *    firebase functions:secrets:set SMS_API_SECRET
 * 4. Deploy: firebase deploy --only functions:sendSMS
 */

const { onRequest } = require('firebase-functions/v2/https');
const { defineSecret } = require('firebase-functions/params');
const admin = require('firebase-admin');
const twilio = require('twilio');
const { authorize, validPhone, validBody } = require('./smsSecurity');

const twilioAccountSid = defineSecret('TWILIO_ACCOUNT_SID');
const twilioAuthToken = defineSecret('TWILIO_AUTH_TOKEN');
const twilioPhoneNumber = defineSecret('TWILIO_PHONE_NUMBER');
const smsApiSecret = defineSecret('SMS_API_SECRET');
const twilioSecrets = [twilioAccountSid, twilioAuthToken, twilioPhoneNumber, smsApiSecret];

const getTwilioConfig = () => ({
  accountSid: twilioAccountSid.value(),
  authToken: twilioAuthToken.value(),
  fromNumber: twilioPhoneNumber.value()
});

// Initialize Firebase Admin (only once)
if (!admin.apps.length) {
  admin.initializeApp();
}

/**
 * Send SMS via Twilio
 *
 * Request body:
 * {
 *   "to": "+27821234567",
 *   "body": "Your message here",
 *   "data": {} // optional additional data
 * }
 */
exports.sendSMS = onRequest({ secrets: twilioSecrets }, async (req, res) => {
  if (!authorize(req, res, smsApiSecret.value())) return;

  try {
    // Validate request body
    const { to, body, data } = req.body || {};

    if (!validPhone(to) || !validBody(body)) {
      res.status(400).json({
        error: 'Provide an E.164 phone number and a message of 1–1600 characters'
      });
      return;
    }

    const { accountSid, authToken, fromNumber } = getTwilioConfig();

    if (!accountSid || !authToken || !fromNumber) {
      console.error('Twilio credentials not configured');
      res.status(500).json({
        error: 'SMS service not properly configured'
      });
      return;
    }

    // Initialize Twilio client
    const client = twilio(accountSid, authToken);

    // Send SMS
    const message = await client.messages.create({
      body: body,
      from: fromNumber,
      to: to
    });

    console.log('SMS sent successfully:', {
      sid: message.sid,
      to: to.slice(0, 5) + '****',
      status: message.status
    });

    // Log to Firebase Analytics (optional)
    try {
      await admin.firestore().collection('sms_logs').add({
        sid: message.sid,
        to: to,
        status: message.status,
        sentAt: admin.firestore.FieldValue.serverTimestamp(),
        data: data || {}
      });
    } catch (logError) {
      console.error('Failed to log SMS:', logError);
      // Don't fail the request if logging fails
    }

    res.status(200).json({
      success: true,
      sid: message.sid,
      status: message.status
    });

  } catch (error) {
    console.error('Error sending SMS:', { code: error.code });

    // Return appropriate error message
    if (error.code === 21211) {
      res.status(400).json({
        error: 'Invalid phone number'
      });
    } else if (error.code === 21608) {
      res.status(400).json({
        error: 'Phone number is unverified. Verify it in Twilio console.'
      });
    } else {
      res.status(500).json({
        error: 'Failed to send SMS'
      });
    }
  }
});

/**
 * Send bulk SMS (batch processing)
 *
 * Request body:
 * {
 *   "recipients": ["+27821234567", "+27829876543"],
 *   "body": "Your message here"
 * }
 */
exports.sendBulkSMS = onRequest({ secrets: twilioSecrets }, async (req, res) => {
  if (!authorize(req, res, smsApiSecret.value())) return;

  try {
    const { recipients, body } = req.body || {};

    if (!Array.isArray(recipients) || recipients.length === 0 ||
        recipients.length > 100 || !recipients.every(validPhone)) {
      res.status(400).json({
        error: 'Provide between 1 and 100 E.164 recipients'
      });
      return;
    }

    if (!validBody(body)) {
      res.status(400).json({
        error: 'Provide a message of 1–1600 characters'
      });
      return;
    }

    const { accountSid, authToken, fromNumber } = getTwilioConfig();

    const client = twilio(accountSid, authToken);

    // Send to all recipients
    const results = await Promise.allSettled(
      recipients.map(to =>
        client.messages.create({
          body: body,
          from: fromNumber,
          to: to
        })
      )
    );

    // Summarize results
    const successful = results.filter(r => r.status === 'fulfilled').length;
    const failed = results.filter(r => r.status === 'rejected').length;

    res.status(200).json({
      success: true,
      total: recipients.length,
      successful: successful,
      failed: failed,
      results: results.map((result, index) => ({
        to: recipients[index],
        success: result.status === 'fulfilled',
        sid: result.status === 'fulfilled' ? result.value.sid : null,
        error: result.status === 'rejected' ? 'Delivery failed' : null
      }))
    });

  } catch (error) {
    console.error('Error sending bulk SMS:', { code: error.code });
    res.status(500).json({
      error: 'Failed to send bulk SMS'
    });
  }
});
