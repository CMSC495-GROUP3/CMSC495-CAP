/**
 * Frontend configuration.
 *
 * APP_NAME is the product-name source of truth for the client. To rebrand,
 * change it here, in policy_assistant/rag/config.py on the Python side, and in
 * the <title> tag in index.html.
 */
export const APP_NAME = 'Handbook'

/** Short line under the title on the empty chat state. */
export const APP_TAGLINE = 'Ask about company policy and get an answer with its source.'

/** localStorage key holding the JWT. */
export const TOKEN_KEY = 'policy_assistant_token'

/**
 * Who a refused or unhelpful answer is handed to. Mirrors ESCALATION_CONTACT
 * in policy_assistant/rag/config.py; change both together.
 */
export const ESCALATION_CONTACT = 'People Operations'
