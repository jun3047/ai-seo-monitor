OAuth & Permissions

Advanced token security via token rotation
Recommended for developers building on or for security-minded organizations – opting into token rotation allows app tokens to automatically expire after they’re issued within your app code. View documentation.

At least one redirect URL needs to be set below before this app can be opted into token rotation
Proof Key for Code Exchange (PKCE)
Enabling this feature lets your app use PKCE during OAuth. This is required if your app redirects to a custom URI scheme like myapp://OAuth, and optional for web-based authentication. See documentation for more details.

OAuth Tokens
OAuth Tokens will be automatically generated when you finish installing your app to your workspace. You’ll use these tokens to authenticate your app.

Redirect URLs
You will need to configure redirect URLs in order to automatically generate the Add to Slack button or to distribute your app. If you pass a URL in an OAuth request, it must (partially) match one of the URLs you enter here. Learn more.

Redirect URLs
You haven’t added any Redirect URLs
범위
Slack 앱의 기능 및 권한은 요청한 범위에 따라 달라집니다.

선택적 상태의 변경 사항은, 해당 권한 범위가 이미 승인된 경우에는 게시된 앱에 즉시 적용됩니다.

봇 토큰 범위
고객님의 앱이 액세스할 수 있는 대상의 범위를 정합니다.

필수

OAuth 범위
설명
 
예

chat:write
SEO 모니터링 봇(으)로 메시지 보내기
사용자 토큰 범위
권한을 부여하는 사용자들을 대신하여 사용자 데이터에 액세스하고 작업하는 범위

필수

OAuth 범위
설명
 
고객님의 사용자 토큰에 OAuth 범위를 추가하지 않았습니다.
범위는 이 앱이 호출할 수 있는 API 메소드를 정의하므로 설치된 워크스페이스에서 사용 가능한 정보와 기능을 정의합니다. 많은 범위는 채널이나 파일과 같은 특정 리소스로 제한됩니다.

Restrict API Token Usage
Slack can limit use of your app’s OAuth tokens to a list of IP addresses and ranges you provide. Slack will then reject Web API method calls from unlisted IP addresses. Learn more.

Allowed IP Address Ranges
You haven’t added any IP address ranges
Revoke All OAuth Tokens
You can revoke all OAuth tokens if you want to invalidate the access any existing tokens have to Slack workspace data. Users will need to grant your app permissions again to use it.