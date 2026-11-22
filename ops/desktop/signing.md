# Desktop app signing

The desktop app workflow (`.github/workflows/desktop.yml`) signs the macOS app with a Developer ID, notarizes it, and staples it. It needs two things from the Blue Door Labs Apple team (`FB99BBDJBA`): a Developer ID Application certificate and an App Store Connect API key. An agent on Mac 1 did these steps on 2026-11-22. Notarization uses the team key that TestFlight already uses. The certificate expires on 2027-12-22. Do the steps again before that date, or when a secret is lost.

Only the Account Holder can create a Developer ID certificate. Do the steps on a Mac.

The bundle identifier is `com.khushaljagota.panels.desktop`. macOS ties notification permissions and saved app data to it. Do not change it.

## 1. Make the Developer ID Application certificate

1. Open Keychain Access. Select Keychain Access → Certificate Assistant → Request a Certificate From a Certificate Authority.
2. Type your email, select "Saved to disk", and save the request file.
3. Open developer.apple.com → Certificates, IDs & Profiles → Certificates → "+".
4. Select "Developer ID Application", then the G2 Sub-CA. Upload the request file.
5. Download the certificate and double-click it. Keychain Access installs it.
6. In Keychain Access → My Certificates, find "Developer ID Application: Blue Door Labs (FB99BBDJBA)".
7. Right-click it → Export. Save it as `DeveloperID.p12` with a strong password. Keep the password in your password manager.

## 2. Make the notarization key

If you still have the `.p8` file of the TestFlight API key, you can use it and skip to step 3.

1. Open App Store Connect → Users and Access → Integrations → App Store Connect API → Team Keys.
2. Select "+". Name it "Panels notarization" and give it the Developer role.
3. Download the `.p8` file. Apple lets you download it only once.
4. Write down the Key ID and the Issuer ID from that page.

## 3. Give them to CI

Run these commands in a terminal on your Mac. They store the values as GitHub secrets of `khushal-jagota/headroom`. Nobody can read a secret back, and pull requests from forks do not receive them.

```sh
base64 -i DeveloperID.p12 | gh secret set MAC_DEVELOPER_ID_P12_BASE64 -R khushal-jagota/headroom
gh secret set MAC_DEVELOPER_ID_P12_PASSWORD -R khushal-jagota/headroom      # paste the .p12 password
gh secret set APPLE_NOTARY_API_KEY_P8 -R khushal-jagota/headroom < AuthKey_XXXXXXXXXX.p8
gh secret set APPLE_NOTARY_API_KEY_ID -R khushal-jagota/headroom            # paste the Key ID
gh secret set APPLE_NOTARY_API_ISSUER_ID -R khushal-jagota/headroom         # paste the Issuer ID
```

Then delete `DeveloperID.p12` and the `.p8` from Downloads. Keep copies in your password manager.
