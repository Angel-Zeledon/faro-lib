# Social sign-in (Google, Apple, Facebook)

Optional. **Off on every installation until the operator turns it on**, so a
copy sold as source code shows only the email + password form unless its buyer
configures it. On our hosted app (app.stockai.es) it is switched on from
`/instalacion`.

How it behaves:

- A button appears only for a provider whose fields are **all** filled **and**
  while `SOCIAL_LOGIN_ENABLED` is `true`. Turning the switch off hides every
  button without deleting the credentials.
- The first sign-in with a provider either **links** to the existing account
  with the same email (only if the provider says it verified that email) or
  **creates** a new company + admin user, exactly like signup does.
- If the existing account had never verified its email here, linking removes
  its password (whoever chose it never proved they own the mailbox). The owner
  sets a new one with "¿Olvidaste tu contraseña?". The activity log records it.
- People see and unlink their providers in **Mi cuenta → Seguridad**. The last
  way into an account cannot be unlinked until a password exists.

## The three redirect URLs

The provider consoles ask for the URL they send people back to. It is always
`<FRONTEND_URL>/api/v1/auth/oauth/<provider>/callback`. For app.stockai.es:

| Provider | Redirect / callback URL |
|---|---|
| Google   | `https://app.stockai.es/api/v1/auth/oauth/google/callback` |
| Apple    | `https://app.stockai.es/api/v1/auth/oauth/apple/callback` |
| Facebook | `https://app.stockai.es/api/v1/auth/oauth/facebook/callback` |

They must match **byte for byte** (https, no trailing slash). `FRONTEND_URL`
on the server must be `https://app.stockai.es`.

## Google

1. Open <https://console.cloud.google.com/>, create (or pick) a project.
2. **APIs & Services → OAuth consent screen** (Google Auth Platform →
   Branding): User type **External**; app name `StockAI`; support email; logo
   optional; **Authorized domain** `stockai.es`; links to your home page,
   privacy policy and terms. Scopes: `openid`, `email`, `profile` (all
   non-sensitive, no Google review needed). Then **Audience → Publish app**
   ("In production"); while in "Testing" only listed test users can sign in.
3. **Credentials → Create credentials → OAuth client ID**, type **Web
   application**.
   - Authorized JavaScript origins: `https://app.stockai.es`
   - Authorized redirect URIs: `https://app.stockai.es/api/v1/auth/oauth/google/callback`
4. Copy the **Client ID** and **Client secret**.

Paste into `/instalacion`: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`.

## Apple

Needs a paid Apple Developer account (<https://developer.apple.com/account>).

1. **Certificates, Identifiers & Profiles → Identifiers → App IDs**: create
   (or reuse) an App ID, e.g. `es.stockai.app`, and tick **Sign in with Apple**.
2. **Identifiers → Services IDs → +**: description `StockAI`, identifier e.g.
   `es.stockai.signin` (this is `APPLE_OAUTH_SERVICE_ID`). Open it, tick
   **Sign in with Apple → Configure**: primary App ID = the one above;
   **Domains**: `app.stockai.es`; **Return URLs**:
   `https://app.stockai.es/api/v1/auth/oauth/apple/callback`. Save. If Apple
   asks you to verify the domain, follow its instructions.
3. **Keys → +**: name `StockAI Sign in`, tick **Sign in with Apple**, configure
   it with the same App ID, register, and **download the .p8 file** (only
   downloadable once). Note the **Key ID**.
4. Your **Team ID** is in the top-right of the developer portal (10
   characters).

Paste into `/instalacion`: `APPLE_OAUTH_SERVICE_ID` (the Services ID, not the
App ID), `APPLE_OAUTH_TEAM_ID`, `APPLE_OAUTH_KEY_ID`, and the whole content of
the .p8 file in `APPLE_OAUTH_PRIVATE_KEY` (BEGIN/END lines included; pasting it
on one line is fine). The server signs a short-lived client secret from it on
every sign-in; nothing expires on your side.

Apple only works over https, and lets people hide their address: a
`…@privaterelay.appleid.com` email is real and is accepted. Apple sends the
person's name only the first time they authorize.

## Facebook (Meta)

1. <https://developers.facebook.com/apps> → **Create app** → use case
   **Authenticate and request data from users with Facebook Login**.
2. **App settings → Basic**: App domains `app.stockai.es` and `stockai.es`;
   Privacy Policy URL and Terms of Service URL; category; icon. Copy **App ID**
   and **App secret**.
3. **Use cases → Facebook Login → Customize → Settings**: Client OAuth login
   **on**, Web OAuth login **on**, Enforce HTTPS **on**, **Valid OAuth Redirect
   URIs**: `https://app.stockai.es/api/v1/auth/oauth/facebook/callback`.
4. **Permissions**: `email` and `public_profile` (standard access is enough).
5. Switch the app to **Live** (top of the dashboard). In Development mode only
   the app's own roles can sign in.

Paste into `/instalacion`: `FACEBOOK_OAUTH_APP_ID`, `FACEBOOK_OAUTH_APP_SECRET`.

Facebook does not return an email for accounts registered with a phone number;
those people are told to use Google, Apple or email + password.

## Turning it on

1. Sign in to app.stockai.es with an address listed in `INSTANCE_ADMIN_EMAILS`.
2. `/instalacion` → **Esta instalación** → card **Inicio de sesión con Google,
   Apple y Facebook**. Paste the values of the providers you want and set
   `SOCIAL_LOGIN_ENABLED` to `true`. Save.
3. **Probar conexión** asks each configured provider whether it recognises the
   client (a wrong secret reads as `auth_failed`) without signing anybody in.
4. Open `/login` in a private window: one button per configured provider.

The same values can be set as environment variables in the deployment's `.env`
instead; a value saved in `/instalacion` wins over the file.
