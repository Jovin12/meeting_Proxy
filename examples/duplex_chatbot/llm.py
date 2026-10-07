import asyncio
from ollama import AsyncClient

MODEL_NAME = "llama3.2:3b"

class ConversationalBot:
    def __init__(self, model_name=MODEL_NAME):
        self.model_name = model_name
        self.client = AsyncClient()
        # Initialize with a system prompt instructing the model on duplex behavior
        self.messages = [
            {
                "role": "system",
                "content": (
                    "You are a responsive, real-time conversational voice assistant. "
                    "You are engaged in a duplex conversation where the user can interrupt you at any time. "
                    "If the user interrupts you, changes the subject, or says something like 'actually no' or 'stop', or interrupts you with any input. "
                    "you must immediately abandon whatever you were talking about, forget your previous train of thought, "
                    "and respond naturally and directly to the user's latest input."
                )
            }
        ]
        self.generation_task = None
        self.is_generating = False

    async def receive_input(self, message: str):
        """Handles new user input, triggering a barge-in interruption if the bot is currently generating."""
        if not message.strip():
            return

        print(f"\n[User]: {message}")

        # 1. Barge-in / Interruption handling: If the bot is currently talking, cancel it.
        if self.is_generating and self.generation_task and not self.generation_task.done():
            print("\n[System: Interrupted by user! Stopping current response...]")
            self.generation_task.cancel()
            try:
                await self.generation_task
            except asyncio.CancelledError:
                pass
            self.is_generating = False

        # 2. Append the new user message to conversation history
        self.messages.append({"role": "user", "content": message})

        # 3. Start a new background generation task
        self.generation_task = asyncio.create_task(self._generate_response())

    async def _generate_response(self):
        """Streams the response from Ollama token by token."""
        self.is_generating = True
        full_response = ""
        
        try:
            print("[Bot]: ", end="", flush=True)
            stream = await self.client.chat(
                model=self.model_name,
                messages=self.messages,
                stream=True
            )
            
            async for chunk in stream:
                content = chunk["message"]["content"]
                if content:
                    print(content, end="", flush=True)
                    full_response += content
            
            print() # Print final newline when done
            
            # Save complete response to history normally
            if full_response:
                self.messages.append({"role": "assistant", "content": full_response})
                
        except asyncio.CancelledError:
            # Tell the model it was cut off mid-sentence
            self.messages.append({"role": "assistant", "content": "[The assistant was interrupted mid-sentence by the user.]"})
            raise
        finally:
            self.is_generating = False

async def main():
    bot = ConversationalBot()
    print(f"Duplex Bot Initialized ({MODEL_NAME}).")
    print("Type your messages below. You can type at *any* time (even while the bot is printing) to interrupt it.")
    print("Type 'q' to quit.\n")

    loop = asyncio.get_running_loop()

    while True:
        # Use run_in_executor to prevent blocking the async event loop with standard input()
        user_input = await loop.run_in_executor(None, input, "\nMSG: ")
        
        if user_input.lower() == "q":
            break

        # Send input to the bot asynchronously
        await bot.receive_input(user_input)

    # Clean up any active generation task on exit
    if bot.generation_task and not bot.generation_task.done():
        bot.generation_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())