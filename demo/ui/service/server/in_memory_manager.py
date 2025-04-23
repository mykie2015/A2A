import asyncio
import datetime
from typing import Tuple, Optional
import uuid
import os  # Import os
from dotenv import load_dotenv # Import dotenv
import httpx # Import httpx
import logging # Import logging

from service.types import Conversation, Event
from common.types import (
    Message,
    Task,
    TextPart,
    TaskState,
    TaskStatus,
    Artifact,
    AgentCard,
    DataPart,
    JSONRPCError,
)
from utils.agent_card import get_agent_card
from service.server.application_manager import ApplicationManager
from service.server import test_image

# Import Langchain OpenAI
from langchain_openai import ChatOpenAI
# Import A2AClient
from common.client import A2AClient, A2ACardResolver

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__) # Get logger instance

class InMemoryFakeAgentManager(ApplicationManager):
  """An implementation of memory based management with fake agent actions

  This implements the interface of the ApplicationManager to plug into
  the AgentServer. This acts as the service contract that the Mesop app
  uses to send messages to the agent and provide information for the frontend.
  """
  _conversations: list[Conversation]
  _messages: list[Message]
  _tasks: list[Task]
  _events: list[Event]
  _pending_message_ids: list[str]
  _agents: list[AgentCard]
  _llm: Optional[ChatOpenAI]

  def __init__(self):
    self._conversations = []
    self._messages = []
    self._tasks = []
    self._events = []
    self._pending_message_ids = []
    self._agents = []
    self._task_map = {}
    self._llm = None

    # Initialize LLM if keys are present
    try:
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openai_api_base = os.getenv("OPENAI_API_BASE")
        llm_model = os.getenv("LLM_MODEL")

        if not openai_api_key:
            logger.warning("OPENAI_API_KEY not found for InMemoryFakeAgentManager LLM fallback.")
        elif not openai_api_base:
             logger.warning("OPENAI_API_BASE not found for InMemoryFakeAgentManager LLM fallback.")
        elif not llm_model:
            logger.warning("LLM_MODEL not found for InMemoryFakeAgentManager LLM fallback.")
        else:
            self._llm = ChatOpenAI(
                model=llm_model,
                api_key=openai_api_key,
                base_url=openai_api_base
            )
            logger.info("InMemoryFakeAgentManager initialized LLM successfully.")
    except Exception as e:
        logger.warning(f"Failed to initialize LLM for InMemoryFakeAgentManager: {e}")

  def create_conversation(self) -> Conversation:
    conversation_id = str(uuid.uuid4())
    c = Conversation(conversation_id=conversation_id, is_active=True)
    self._conversations.append(c)
    return c

  def sanitize_message(self, message: Message) -> Message:
    if not message.metadata:
      message.metadata = {}
    message.metadata.update({'message_id': str(uuid.uuid4())})
    return message

  async def process_message(self, message: Message):
    self._messages.append(message)
    message_id = message.metadata['message_id']
    self._pending_message_ids.append(message_id)
    conversation_id = (
        message.metadata['conversation_id']
        if 'conversation_id' in message.metadata
        else None
    )
    conversation = self.get_conversation(conversation_id)
    if conversation:
      conversation.messages.append(message)
    
    self._events.append(Event(
        id=str(uuid.uuid4()),
        actor="host",
        content=message,
        timestamp=datetime.datetime.utcnow().timestamp(),
    ))
    
    task_id = str(uuid.uuid4())
    task = Task(
              id=task_id,
              sessionId=conversation_id,
              status=TaskStatus(
                  state=TaskState.SUBMITTED,
                  message=message,
              ),
              history=[message],
          )
    self.add_task(task)
    self._task_map[message_id] = task_id

    response = None
    agent_used_name = "LLM Fallback" # For logging

    # === Start of CORRECT routing logic ===
    if not self._agents: 
        # --- LLM Fallback logic (No agents registered) ---
        agent_used_name = "LLM Fallback"
        logger.info(f"No agents registered. Using LLM fallback for message: {message.parts[0].text if message.parts else '[no text part]'}")
        if self._llm: 
            try:
                prompt = "You are a helpful assistant managing an agent system. Politely inform the user that no agents are currently registered or available to handle their request."
                llm_response = await self._llm.ainvoke(prompt)
                response_text = llm_response.content
                logger.info(f"LLM fallback response: {response_text}")
                response = Message(role="agent", parts=[TextPart(text=response_text)])
                task.status.state = TaskState.COMPLETED # LLM fallback is considered complete
            except Exception as e:
                logger.error(f"Error during LLM fallback: {e}", exc_info=True)
                response = Message(role="agent", parts=[TextPart(text="Sorry, no agents are registered and I encountered an error trying to generate a specific response.")])
                task.status.state = TaskState.ERROR
                task.status.error = JSONRPCError(code=-32603, message=f"Internal error during LLM fallback: {e}")
        else:
            # Hardcoded response if LLM is not available
            logger.warning("LLM not available for fallback.")
            response = Message(role="agent", parts=[TextPart(text="No agents are currently registered or available.")])
            task.status.state = TaskState.COMPLETED # Hardcoded response is complete
    else:
        # --- Route to first registered agent --- 
        target_agent = self._agents[0]
        agent_used_name = target_agent.name or target_agent.url
        logger.info(f"Found registered agent '{agent_used_name}'. Routing message...")
        try:
            if not isinstance(target_agent, AgentCard):
                 logger.error(f"Stored agent object is not an AgentCard: {type(target_agent)}")
                 raise TypeError("Stored agent is not an AgentCard object")
                 
            client = A2AClient(agent_card=target_agent)
            payload = {
                "id": task_id,
                "sessionId": conversation_id,
                "acceptedOutputModes": ["text", "text/plain"], 
                "message": message.model_dump(exclude_none=True),
            }
            logger.info(f"Sending task to agent '{agent_used_name}': {payload}")
            task_response = await client.send_task(payload)

            if task_response.error:
                logger.error(f"Error received from agent '{agent_used_name}': {task_response.error}")
                response_text = f"Error communicating with agent: {task_response.error.message}"
                response = Message(role="agent", parts=[TextPart(text=response_text)])
                task.status.state = TaskState.ERROR
                task.status.error = task_response.error
            elif task_response.result:
                 response_found = False
                 task_result = task_response.result # Convenience variable
                 
                 # --- Start NEW: Check for INPUT_REQUIRED state first --- 
                 if task_result.status and task_result.status.state == TaskState.INPUT_REQUIRED and task_result.status.message:
                     response = task_result.status.message
                     # Ensure the role is appropriate for display
                     if response.role != "agent":
                         response.role = "agent"
                     logger.info(f"Response found in status.message (INPUT_REQUIRED) for agent '{agent_used_name}'. TaskId: {task_id}")
                     task.status.state = TaskState.INPUT_REQUIRED # Keep the state
                     task.artifacts = task_result.artifacts # Save artifacts if any
                     response_found = True
                 # --- End NEW: Check for INPUT_REQUIRED ---

                 # 1. Check History (if not already found)
                 elif not response_found and task_result.history:
                     response = task_result.history[-1]
                     logger.info(f"Response found in HISTORY for agent '{agent_used_name}'. TaskId: {task_id}")
                     task.status.state = TaskState.COMPLETED 
                     task.artifacts = task_result.artifacts # Still save artifacts if any
                     response_found = True
                 # 2. If no history, check Artifacts (if not already found)
                 elif not response_found and task_result.artifacts:
                     logger.info(f"No response in history or status.message, checking ARTIFACTS for agent '{agent_used_name}'. TaskId: {task_id}")
                     # Try to find the first TextPart in artifacts
                     for artifact in task_result.artifacts:
                         if artifact.parts:
                             for part in artifact.parts:
                                 if isinstance(part, TextPart):
                                     response = Message(role="agent", parts=[part])
                                     # Log snippet of the found text
                                     log_text = part.text[:100] + '...' if len(part.text) > 100 else part.text
                                     logger.info(f"Response found in ARTIFACTS for agent '{agent_used_name}'. TaskId: {task_id}. Content snippet: '{log_text}'")
                                     task.status.state = TaskState.COMPLETED
                                     task.artifacts = task_result.artifacts # Save all artifacts
                                     response_found = True
                                     break # Use the first text part found
                         if response_found:
                             break # Stop checking artifacts once found
                     
                     if not response_found:
                         logger.warning(f"Agent '{agent_used_name}' returned artifacts, but no displayable TextPart found. TaskId: {task_id}. Artifacts: {task_result.artifacts}")
                         response_text = "Agent returned data, but not in a displayable text format."
                         response = Message(role="agent", parts=[TextPart(text=response_text)])
                         task.status.state = TaskState.COMPLETED # Still completed, just not displayable
                         task.artifacts = task_result.artifacts # Save artifacts
                 
                 # 3. If still no response found
                 elif not response_found:
                    logger.warning(f"Agent '{agent_used_name}' responded successfully, but the returned Task object contained NO usable message in history, artifacts, or status.message (for INPUT_REQUIRED state). TaskId: {task_id}. Check agent logs if response was expected. Full Task Result: {task_result}")
                    response_text = "Agent processed the request but returned no messages or artifacts."
                    response = Message(role="agent", parts=[TextPart(text=response_text)])
                    task.status.state = TaskState.COMPLETED
            else:
                # Handle unexpected successful response structure (e.g., result is None)
                logger.warning(f"Agent '{agent_used_name}' response structure unexpected (e.g., result is None). TaskId: {task_id}. Full JSON-RPC Response: {task_response}")
                response_text = "Agent processed the request but did not return a valid result structure."
                response = Message(role="agent", parts=[TextPart(text=response_text)])
                task.status.state = TaskState.COMPLETED

        except Exception as e:
            logger.error(f"Exception during communication with agent '{agent_used_name}'. TaskId: {task_id}. Error: {e}", exc_info=True)
            response_text = f"Failed to communicate with the registered agent: {e}"
            response = Message(role="agent", parts=[TextPart(text=response_text)])
            # --- Safely set TaskState.ERROR ---
            try:
                task.status.state = TaskState.ERROR
            except Exception as state_err:
                logger.error(f"Failed to set TaskState.ERROR: {state_err}", exc_info=True)
            # --- End safe set ---
            task.status.error = JSONRPCError(code=-32603, message=f"Internal error sending to agent: {e}")
        # --- End agent routing ---
    # === End of CORRECT routing logic ===

    # Ensure response is not None before proceeding 
    if response is None:
        logger.error("Failed to generate any response (agent or fallback).")
        response = Message(role="agent", parts=[TextPart(text="Sorry, I encountered an internal error processing your request.")])
        # --- Safely set TaskState.ERROR ---
        try:
            task.status.state = TaskState.ERROR
        except Exception as state_err:
            logger.error(f"Failed to set TaskState.ERROR: {state_err}", exc_info=True)
        # --- End safe set ---
        task.status.error = JSONRPCError(code=-32603, message="Internal error: No response generated.")

    response.metadata = {**message.metadata, **{'message_id': str(uuid.uuid4())}}
    if conversation:
      conversation.messages.append(response)
    
    self._events.append(Event(
        id=str(uuid.uuid4()),
        actor="agent", # Changed actor to agent
        content=response,
        timestamp=datetime.datetime.utcnow().timestamp(),
    ))
    
    self._pending_message_ids.remove(message.metadata['message_id'])
    
    # Update task status (most states set above)
    task.history.append(response) # Add final response to history
    self.update_task(task)

  def add_task(self, task: Task):
    self._tasks.append(task)

  def update_task(self, task: Task):
    for i, t in enumerate(self._tasks):
      if t.id == task.id:
        self._tasks[i] = task
        return

  def add_event(self, event: Event):
    self._events.append(event)

  def get_conversation(
      self,
      conversation_id: Optional[str]
  ) -> Optional[Conversation]:
    if not conversation_id:
      return None
    return next(
        filter(lambda c: c.conversation_id == conversation_id,
               self._conversations), None)

  def get_pending_messages(self) -> list[Tuple[str,str]]:
    rval = []
    for message_id in self._pending_message_ids:
      if message_id in self._task_map:
        task_id = self._task_map[message_id]
        task = next(filter(lambda x: x.id == task_id, self._tasks), None)
        if not task:
          rval.append((message_id, ""))
        elif task.history and task.history[-1].parts:
          if len(task.history) == 1:
            rval.append((message_id, "Working..."))
          else:
            part = task.history[-1].parts[0]
            rval.append((
                message_id,
                part.text if part.type == "text" else "Working..."))
      else:
        rval.append((message_id, ""))
      return rval
    return self._pending_message_ids

  def register_agent(self, url):
    agent_data = get_agent_card(url)
    if not agent_data.url:
      agent_data.url = url
    self._agents.append(agent_data)

  @property
  def agents(self) -> list[AgentCard]:
    return self._agents

  @property
  def conversations(self) -> list[Conversation]:
    return self._conversations

  @property
  def tasks(self) -> list[Task]:
    return self._tasks

  @property
  def events(self) -> list[Event]:
    return self._events

