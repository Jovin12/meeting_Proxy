from sentence_transformers import SentenceTransformer
import chromadb


class TranscriptRetriever:
    """
    Creates conversational chunks from a transcript, stores them in ChromaDB,
    and retrieves the most relevant transcript sections for a given note.
    """

    COLLECTION_NAME = "meeting_transcript"
    EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    WINDOW_SIZE = 3

    def __init__(self):
        # Local embedding model
        self.model = SentenceTransformer(
            self.EMBEDDING_MODEL
        )

        # ChromaDB client
        self.client = chromadb.Client()

        # Create the collection
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME
        )

    def chunk_transcript(
        self,
        transcript: str,
        window_size: int = WINDOW_SIZE,
    ) -> list[str]:
        """
        Split the transcript into overlapping conversational windows.

        Example:

            Alice: We should use PostgreSQL.
            Bob: I agree.
            Alice: I'll update the architecture.

        becomes:

            Alice: We should use PostgreSQL.
            Bob: I agree.
            Alice: I'll update the architecture.
        """

        lines = [
            line.strip()
            for line in transcript.splitlines()
            if line.strip()
        ]

        if not lines:
            return []

        # If the transcript is shorter than the requested window,
        # treat the entire transcript as one chunk.
        if len(lines) <= window_size:
            return ["\n".join(lines)]

        chunks = []

        for start in range(len(lines) - window_size + 1):
            end = start + window_size

            chunk = "\n".join(lines[start:end])
            chunks.append(chunk)

        return chunks

    def clear_collection(self):
        """
        Remove all previously indexed transcript data.
        """

        self.client.delete_collection(
            name=self.COLLECTION_NAME
        )

        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME
        )

    def index_transcript(self, transcript: str):
        """
        Clear the previous transcript and index the new transcript.
        """

        # Prevent old transcript chunks from contaminating retrieval.
        self.clear_collection()

        chunks = self.chunk_transcript(transcript)

        if not chunks:
            return

        embeddings = self.model.encode(
            chunks
        ).tolist()

        ids = [
            f"chunk_{i}"
            for i in range(len(chunks))
        ]

        self.collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
        )

    def search(self,note: str,top_k: int = 3,) -> list[dict]:

        if not note.strip():
            return []

        if self.collection.count() == 0:
            return []

        query_embedding = self.model.encode(
            [note]
        ).tolist()

        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=min(
                top_k,
                self.collection.count(),
            ),
            include=[
                "documents",
                "distances",
            ],
        )

        documents = results["documents"][0]
        distances = results["distances"][0]

        return [
            {
                "text": document,
                "distance": distance,
            }
            for document, distance in zip(
                documents,
                distances,
            )
        ]