def calculate(rows, payload):
    candidates={}; ballot_totals={}
    for row in rows:
        candidates[row["candidate"]]=candidates.get(row["candidate"],0)+int(row["votes"])
        ballot_totals[row["ballot_type"]]=ballot_totals.get(row["ballot_type"],0)+int(row["votes"])
    actual=sum(candidates.values()); expected=sum(payload.get("expected_precinct_totals",{}).values());
    return {"candidate_totals":candidates,"ballot_type_totals":ballot_totals,"total_votes":actual,"balanced":actual==expected,"difference":actual-expected}
