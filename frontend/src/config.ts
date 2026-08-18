/**
 * Frontend configuration.
 *
 * APP_NAME is the product-name source of truth for the client. The product name
 * is still pending — to rebrand, change it here, in src/config.py on the Python
 * side, and in the <title> tag in index.html.
 */
export const APP_NAME = 'Policy Assistant'

/** Short line under the title on the empty chat state. */
export const APP_TAGLINE = 'Ask about company policy and get an answer with its source.'

/** localStorage key holding the JWT. */
export const TOKEN_KEY = 'policy_assistant_token'
