import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# We initialize the client if GROQ_API_KEY is available.
api_key = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=api_key) if api_key else None

def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict:
    """
    Given the 4 context dictionaries, construct a prompt for the LLM to generate the next message.
    Returns:
        {
            "body": "...",
            "cta": "...",
            "send_as": "...",
            "suppression_key": "...",
            "rationale": "..."
        }
    """
    
    if not client:
        return {
            "body": "System Configuration Error: GROQ_API_KEY not found in .env",
            "cta": "none",
            "send_as": "vera",
            "suppression_key": trigger.get("suppression_key", ""),
            "rationale": "Error state"
        }

    # Build the context payload for the LLM
    context_str = f"""
<category_context>
{json.dumps(category, indent=2)}
</category_context>

<merchant_context>
{json.dumps(merchant, indent=2)}
</merchant_context>

<trigger_context>
{json.dumps(trigger, indent=2)}
</trigger_context>
"""
    if customer:
        context_str += f"""
<customer_context>
{json.dumps(customer, indent=2)}
</customer_context>
"""

    system_prompt = """
You are an AI generating WhatsApp messages for 'Vera', an AI assistant for magicpin merchants.
Your task is to write a single proactive WhatsApp message to a merchant (or to a customer on behalf of the merchant) based on the provided contexts.

Constraints:
1. Specificity: Use exact numbers, dates, headlines, or peer stats from the context. No generic "10% off" if a specific price exists.
2. Voice match: Use the tone and allowed vocabulary from the CategoryContext.
3. Merchant fit: Personalize based on the merchant's numbers, offers, and language preference. Mix English and Hindi if the preference is "hi-en mix" or "hi".
4. Trigger relevance: Explicitly state *why* you are messaging them now based on the TriggerContext.
5. Engagement compulsion: Use curiosity, social proof, loss aversion, or effort externalization. End with a single clear Call-to-Action (binary YES/STOP or simple).
6. Target: If customer context is provided, message the customer directly acting as the merchant. Otherwise, message the merchant acting as Vera.

Output MUST be a valid JSON object with EXACTLY these keys:
- "body": The WhatsApp message text
- "cta": The call to action type (e.g. "open_ended", "binary", "none")
- "send_as": Either "vera" (if messaging merchant) or "merchant_on_behalf" (if messaging customer)
- "suppression_key": The suppression key from the trigger
- "rationale": 1-2 sentence explanation of your design choices (why this message works)

Output nothing but the raw JSON object, no markdown blocks.
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile", # Groq model
            max_tokens=1000,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context_str}
            ]
        )
        
        output_text = response.choices[0].message.content.strip()
            
        result = json.loads(output_text)
        
        # Ensure fallback for suppression_key
        if "suppression_key" not in result:
             result["suppression_key"] = trigger.get("suppression_key", "")
             
        return result
    except Exception as e:
        print(f"Error calling LLM: {e}")
        # Fallback response if LLM fails
        return {
            "body": f"Hi {merchant.get('identity', {}).get('name', 'Merchant')}, I have some new updates for your profile. Reply YES to know more.",
            "cta": "binary",
            "send_as": "vera",
            "suppression_key": trigger.get("suppression_key", ""),
            "rationale": f"Fallback response due to LLM error: {str(e)}"
        }

def reply_compose(conversation_history: list) -> dict:
    """
    Given the conversation history, construct a prompt for the LLM to decide the next action.
    Returns:
        {
            "action": "...",
            "body": "...",
            "cta": "...",
            "rationale": "..."
        }
    """
    if not client:
        return {
            "action": "send",
            "body": "System Configuration Error: GROQ_API_KEY not found in .env",
            "cta": "none",
            "rationale": "Error state"
        }

    history_str = json.dumps(conversation_history, indent=2)
    system_prompt = """
You are 'Vera', an AI assistant for magicpin merchants. You are replying to a merchant.
You must analyze the conversation history and decide the next action.
Actions allowed:
- "send": Send a reply message to the merchant.
- "wait": Do not reply immediately, wait for more context.
- "end": End the conversation (e.g., if the user is hostile, explicitly asked to stop, or if they are just an auto-responder bot).

Rules:
1. If the merchant sends an auto-reply repeatedly (like "Thank you for contacting us..."), you MUST output action "end" to stop the conversation and avoid infinite loops.
2. If the merchant shows intent to proceed (e.g., "Ok lets do it. Whats next?"), you MUST output action "send" and the body should be ACTION-oriented (use words like "done", "sending", "proceed", "next"). Do NOT use qualifying words like "would you", "can you".
3. If the merchant is hostile (e.g., "Stop messaging me", "spam"), you MUST output action "end", OR action "send" with an apology ("sorry", "apologize", "won't message again").

Output MUST be a valid JSON object with exactly these keys:
- "action": "send", "wait", or "end"
- "body": The message text (if action is "send", otherwise "")
- "cta": "none", "open_ended", or "binary"
- "rationale": "Why you chose this action"

Output nothing but the raw JSON object.
"""
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Conversation History:\n{history_str}"}
            ]
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print(f"Error calling LLM for reply: {e}")
        return {
            "action": "send",
            "body": "I'm having trouble connecting right now. Let's talk later.",
            "cta": "none",
            "rationale": f"Fallback response due to LLM error: {str(e)}"
        }

