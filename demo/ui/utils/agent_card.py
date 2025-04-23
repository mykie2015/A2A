import requests
import logging # Import logging
from urllib.parse import urlparse # Import url tools
from common.types import AgentCard

logger = logging.getLogger(__name__) # Get logger

def get_agent_card(remote_agent_address: str) -> AgentCard:
  """Get the agent card."""
  # Strip leading/trailing and remove internal whitespace
  cleaned_address = "".join(remote_agent_address.split())
  logger.info(f"Original address: '{remote_agent_address}', Cleaned address: '{cleaned_address}'")
  remote_agent_address = cleaned_address # Use the cleaned address

  # Simple scheme check and construction
  if not remote_agent_address.startswith(("http://", "https://")):
    base_url = f"http://{remote_agent_address}"
    logger.info(f"No scheme found, defaulting to: {base_url}")
  else:
    base_url = remote_agent_address

  # Ensure trailing slash for joining
  if not base_url.endswith('/'):
    base_url += '/'
  
  agent_card_url = base_url + ".well-known/agent.json"
  logger.info(f"Constructed agent card URL: {agent_card_url}")

  try:
    response = requests.get(agent_card_url, timeout=10) # Increased timeout slightly
    response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
    
    agent_card_data = response.json()
    logger.info(f"Received agent card data: {agent_card_data}")
    # Add validation before returning
    card = AgentCard(**agent_card_data)
    # Store the original URL if not present in the card itself
    if not card.url:
      card.url = base_url.rstrip('/')
    return card
  except requests.exceptions.Timeout:
    logger.error(f"Timeout fetching agent card from {agent_card_url}")
    raise ValueError(f"Timeout connecting to agent at {remote_agent_address}")
  except requests.exceptions.RequestException as e:
    logger.error(f"Error fetching agent card from {agent_card_url}: {e}", exc_info=True)
    # Attempt to provide a more specific error message if possible
    err_msg = str(e)
    if response is not None:
      err_msg = f"{response.status_code} {response.reason}"
    raise ValueError(f"Cannot connect to agent as {err_msg} ({remote_agent_address})") from e
  except ValueError as e: # Catch JSON decoding errors specifically
    logger.error(f"Error decoding agent card JSON from {agent_card_url}: {e}", exc_info=True)
    raise ValueError(f"Could not parse agent card JSON from {remote_agent_address}") from e
  except Exception as e:
    logger.error(f"Unexpected error processing agent card from {agent_card_url}: {e}", exc_info=True)
    raise ValueError(f"Unexpected error getting agent card from {remote_agent_address}") from e
