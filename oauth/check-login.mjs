// Ask the credential provider to validate/refresh its session; never print tokens.
import { openaiCredentials } from '@openai-oauth/local';

try {
  const session = await openaiCredentials().getSession();
  if (!session?.accessToken) throw new Error('Missing access token');
  console.log('ChatGPT OAuth credentials are available.');
} catch (error) {
  const message = String(error?.message || '').toLowerCase();
  const authFailure = /auth|token|credential|enoent|invalid_grant|sign in/.test(message);
  console.error(authFailure ? 'ChatGPT login is required.' : 'Unable to verify ChatGPT login.');
  process.exitCode = authFailure ? 2 : 3;
}
