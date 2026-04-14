from neo4j import GraphDatabase, Driver

from config import settings


class Neo4jService:
    def __init__(self) -> None:
        self._driver: Driver | None = None

    def _d(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        return self._driver

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def ensure_schema(self) -> None:
        stmts = [
            "CREATE CONSTRAINT service_id IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT incident_id IF NOT EXISTS FOR (i:Incident) REQUIRE i.id IS UNIQUE",
            "CREATE CONSTRAINT ticket_id IF NOT EXISTS FOR (t:Ticket) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT message_id IF NOT EXISTS FOR (m:Message) REQUIRE m.id IS UNIQUE",
        ]
        with self._d().session() as session:
            for q in stmts:
                try:
                    session.run(q)
                except Exception:
                    pass

    def upsert_service(self, service_id: str, name: str | None = None) -> None:
        with self._d().session() as session:
            session.run(
                """
                MERGE (s:Service {id: $id})
                SET s.name = coalesce($name, s.name, $id)
                """,
                id=service_id,
                name=name,
            )

    def upsert_incident(self, incident_id: str, title: str | None = None) -> None:
        with self._d().session() as session:
            session.run(
                """
                MERGE (i:Incident {id: $id})
                SET i.title = coalesce($title, i.title, $id)
                """,
                id=incident_id,
                title=title,
            )

    def upsert_ticket(self, ticket_id: str, key: str | None = None) -> None:
        with self._d().session() as session:
            session.run(
                """
                MERGE (t:Ticket {id: $id})
                SET t.key = coalesce($key, t.key, $id)
                """,
                id=ticket_id,
                key=key,
            )

    def upsert_message(self, message_id: str, channel: str | None = None) -> None:
        with self._d().session() as session:
            session.run(
                """
                MERGE (m:Message {id: $id})
                SET m.channel = coalesce($channel, m.channel, '')
                """,
                id=message_id,
                channel=channel,
            )

    def link_service_depends(self, from_id: str, to_id: str) -> None:
        with self._d().session() as session:
            session.run(
                """
                MATCH (a:Service {id: $from_id}), (b:Service {id: $to_id})
                MERGE (a)-[:SERVICE_DEPENDS_ON]->(b)
                """,
                from_id=from_id,
                to_id=to_id,
            )

    def link_caused_by(self, incident_id: str, service_id: str) -> None:
        with self._d().session() as session:
            session.run(
                """
                MATCH (i:Incident {id: $iid}), (s:Service {id: $sid})
                MERGE (i)-[:CAUSED_BY]->(s)
                """,
                iid=incident_id,
                sid=service_id,
            )

    def link_related_incidents(self, a_id: str, b_id: str) -> None:
        with self._d().session() as session:
            session.run(
                """
                MATCH (i1:Incident {id: $a}), (i2:Incident {id: $b})
                MERGE (i1)-[:RELATED_TO]->(i2)
                """,
                a=a_id,
                b=b_id,
            )

    def link_reported_in(self, incident_id: str, ticket_id: str) -> None:
        with self._d().session() as session:
            session.run(
                """
                MATCH (i:Incident {id: $iid}), (t:Ticket {id: $tid})
                MERGE (i)-[:REPORTED_IN]->(t)
                """,
                iid=incident_id,
                tid=ticket_id,
            )

    def link_message_to_incident(self, message_id: str, incident_id: str) -> None:
        with self._d().session() as session:
            session.run(
                """
                MATCH (m:Message {id: $mid}), (i:Incident {id: $iid})
                MERGE (m)-[:RELATED_TO]->(i)
                """,
                mid=message_id,
                iid=incident_id,
            )

    def query_context_for_services(self, service_names: list[str], limit: int = 30) -> dict:
        if not service_names:
            return {"paths": [], "summary": ""}
        with self._d().session() as session:
            result = session.run(
                """
                UNWIND $services AS svc
                MATCH (s:Service)
                WHERE s.id = svc OR coalesce(s.name, '') = svc
                OPTIONAL MATCH (s)-[r]-(n)
                RETURN DISTINCT s.id AS sid,
                                type(r) AS rel,
                                labels(n)[0] AS nl,
                                coalesce(n.id, n.name, toString(id(n))) AS nid
                LIMIT $lim
                """,
                services=service_names,
                lim=limit,
            )
            lines: list[str] = []
            seen: set[str] = set()
            for r in result:
                sid = r["sid"]
                rel = r["rel"]
                nl = r["nl"]
                nid = r["nid"]
                if rel and nl:
                    row = f"Service({sid})-[{rel}]->{nl}({nid})"
                else:
                    row = f"Service({sid})"
                if row not in seen:
                    seen.add(row)
                    lines.append(row)
        summary = "\n".join(lines[:20])
        return {"paths": lines, "summary": summary}


neo4j_service = Neo4jService()
