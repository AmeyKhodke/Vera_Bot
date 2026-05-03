import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# We initialize the client if GROQ_API_KEY is available.
api_key = os.environ.get("GROQ_API_KEY", "").strip()
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
<category_context>{json.dumps(category)}</category_context>
<merchant_context>{json.dumps(merchant)}</merchant_context>
<trigger_context>{json.dumps(trigger)}</trigger_context>
"""
    if customer:
        context_str += f"<customer_context>{json.dumps(customer)}</customer_context>"

    system_prompt = """
You are an AI generating WhatsApp messages for 'Vera', an AI assistant for magicpin merchants.
Your task is to write a single proactive WhatsApp message to a merchant (or to a customer on behalf of the merchant) based on the provided contexts.

Core Principles:
1. SPECIFICITY: Use exact numbers, dates, headlines, or peer stats from the context. Instead of "your views are up", say "your views increased by 18% in the last 7 days". Use exact offer prices like "₹299". Use source citations if available (e.g., "JIDA Oct 2026").
2. CLINICAL/BUSINESS ANCHOR: For Decision Quality, don't just ask to book. Provide a brief, high-value reason (e.g., "3-mo fluoride recall cuts caries 38% better" or "exam-stress bruxism spike in Nov").
3. CATEGORY FIT: Use the tone and clinical/domain-specific vocabulary from CategoryContext. Use "Dr." for medical categories.
4. MERCHANT FIT: Explicitly mention the merchant's locality (e.g., "in Lajpat Nagar") and the owner's name if provided. Honor language preferences (Hinglish for 'hi-en mix').
5. TRIGGER RELEVANCE: Explicitly anchor the message on the trigger. Why are we talking NOW?
6. ENGAGEMENT COMPULSION: Use curiosity, social proof (compare with peer stats), loss aversion, or effort externalization. Use a single, low-friction CTA.

Output MUST be a valid JSON object with EXACTLY these keys:
- "body": The WhatsApp message text
- "cta": The call to action type (e.g. "open_ended", "binary", "none")
- "send_as": Either "vera" (if messaging merchant) or "merchant_on_behalf" (if messaging customer)
- "suppression_key": The suppression key from the trigger
- "rationale": 1-2 sentence explanation of design choices.

Output nothing but the raw JSON object.
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
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

def reply_compose(conversation_history: list, category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict:
    """
    Given the conversation history and context, decide the next action.
    """
    if not client:
        return {
            "action": "send",
            "body": "System Configuration Error: GROQ_API_KEY not found in .env",
            "cta": "none",
            "rationale": "Error state"
        }

    history = conversation_history
    
    # --- HARDCODED GUARDRAILS (No LLM needed for these) ---
    if history:
        last_msg = history[-1].get("body", "").lower()
        
        # 1. Hostility / STOP Handling — check incoming message
        hostile_words = ["stop", "unsubscribe", "spam", "useless", "don't contact", "do not contact", "remove me"]
        if any(word in last_msg for word in hostile_words):
            return {"action": "end", "body": "", "cta": "none", "rationale": "Hostility/opt-out detected."}
            
        # 2. Auto-Reply Detection:
        #    (a) Single message looks like a bot auto-responder
        auto_reply_phrases = [
            "thank you for contacting", "thank you for reaching out",
            "we will get back", "our team will respond", "auto-reply",
            "this is an automated", "out of office", "currently unavailable"
        ]
        if any(phrase in last_msg for phrase in auto_reply_phrases):
            return {"action": "end", "body": "", "cta": "none", "rationale": "Auto-reply message detected."}
        
        #    (b) Last 2 user messages in same conversation are identical
        user_msgs = [m.get("body", "") for m in history if m.get("role") in ("user", "merchant", "customer")]
        if len(user_msgs) >= 2 and user_msgs[-1].strip().lower() == user_msgs[-2].strip().lower():
            return {"action": "end", "body": "", "cta": "none", "rationale": "Repeated identical message — auto-reply."}
        
        # 3. Commitment / Intent Transition — merchant says "let's do it", "proceed", etc.
        commitment_phrases = [
            "ok lets do it", "ok let's do it", "lets do it", "let's do it",
            "whats next", "what's next", "proceed", "go ahead", "sounds good",
            "yes please", "confirmed", "book it", "do it", "sign me up"
        ]
        if any(phrase in last_msg for phrase in commitment_phrases):
            merchant_name = merchant.get("identity", {}).get("owner_first_name", "")
            greeting = f"Dr. {merchant_name}" if merchant_name else "there"
            return {
                "action": "send",
                "body": f"Proceeding now, {greeting}! I'll draft the outreach messages and send you a preview shortly. You'll have full control to approve before anything goes live.",
                "cta": "none",
                "rationale": "Commitment detected — switching to ACTION mode immediately."
            }
    # -------------------------------------------------------

    context_data = {
        "category": category,
        "merchant": merchant,
        "trigger": trigger,
        "customer": customer,
        "history": history[-5:] # Only last 5 messages
    }
    
    context_str = json.dumps(context_data)
    
    system_prompt = """
You are 'Vera', an AI assistant for magicpin. You are continuing a conversation.
Your goal is to be helpful, concise, and action-oriented.

Roles:
- If the last message was from 'merchant', you are 'Vera' helping them.
- If the last message was from 'customer', you are the 'Merchant' replying to your customer.

Actions allowed:
- "send": Send a reply.
- "wait": Wait for more context (use if the user said "hold on" or similar).
- "end": Stop the conversation (user said "stop", "unsubscribe", or if they are just an auto-responder).

Rules:
1. TRIGGER GROUNDING: Always keep the original trigger in mind. If the conversation started about a "regulation change", don't suddenly switch to generic talk.
2. INTENT DETECTION:
   - If the user (customer) picks a slot or expresses intent to book, confirm the EXACT details (time, date, service) and say what happens next.
   - If the merchant says "ok let's do it" or similar commitment, switch to ACTION immediately.
   - ACTION MODE STRICTURE: In action mode, use words like "Done", "Processed", "Proceeding", "Next". 
   - CRITICAL: DO NOT use qualifying phrases like "would you", "do you", "can you", "what if", or "how about" as these indicate you are still qualifying rather than acting.
3. AUTO-REPLY DETECTION: If the last 2-3 messages from the user are identical or look like "Thank you for contacting...", use action "end".
4. SPECIFICITY: Use numbers/data from the context.
5. VOICE: Match the category tone and merchant language preference (English/Hindi mix).

Output MUST be a valid JSON object:
- "action": "send", "wait", or "end"
- "body": The message text (if action is "send")
- "cta": "none", "open_ended", or "binary"
- "rationale": Why you chose this move.

Output nothing but the raw JSON object.
"""
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            max_tokens=800,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context and History:\n{context_str}"}
            ]
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        print(f"Error calling LLM for reply: {e}")
        return {
            "action": "send",
            "body": "I'm processing that. I'll get back to you shortly.",
            "cta": "none",
            "rationale": f"Fallback due to error: {str(e)}"
        }

