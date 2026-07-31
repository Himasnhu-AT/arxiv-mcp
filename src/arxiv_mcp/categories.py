"""Bundled arXiv subject taxonomy for validating and discovering ``cat:`` filters.

Source of truth: https://arxiv.org/category_taxonomy. This is a curated subset
covering the actively used categories; add more as needed.
"""

from __future__ import annotations

TAXONOMY: dict[str, str] = {
    # Computer Science
    "cs.AI": "Artificial Intelligence",
    "cs.AR": "Hardware Architecture",
    "cs.CC": "Computational Complexity",
    "cs.CE": "Computational Engineering, Finance, and Science",
    "cs.CG": "Computational Geometry",
    "cs.CL": "Computation and Language",
    "cs.CR": "Cryptography and Security",
    "cs.CV": "Computer Vision and Pattern Recognition",
    "cs.CY": "Computers and Society",
    "cs.DB": "Databases",
    "cs.DC": "Distributed, Parallel, and Cluster Computing",
    "cs.DL": "Digital Libraries",
    "cs.DM": "Discrete Mathematics",
    "cs.DS": "Data Structures and Algorithms",
    "cs.ET": "Emerging Technologies",
    "cs.FL": "Formal Languages and Automata Theory",
    "cs.GT": "Computer Science and Game Theory",
    "cs.GR": "Graphics",
    "cs.HC": "Human-Computer Interaction",
    "cs.IR": "Information Retrieval",
    "cs.IT": "Information Theory",
    "cs.LG": "Machine Learning",
    "cs.LO": "Logic in Computer Science",
    "cs.MA": "Multiagent Systems",
    "cs.MM": "Multimedia",
    "cs.MS": "Mathematical Software",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.NI": "Networking and Internet Architecture",
    "cs.OS": "Operating Systems",
    "cs.PL": "Programming Languages",
    "cs.RO": "Robotics",
    "cs.SC": "Symbolic Computation",
    "cs.SE": "Software Engineering",
    "cs.SD": "Sound",
    "cs.SI": "Social and Information Networks",
    "cs.SY": "Systems and Control",
    # Statistics
    "stat.AP": "Applications",
    "stat.CO": "Computation",
    "stat.ML": "Machine Learning",
    "stat.ME": "Methodology",
    "stat.TH": "Statistics Theory",
    # Electrical Engineering and Systems Science
    "eess.AS": "Audio and Speech Processing",
    "eess.IV": "Image and Video Processing",
    "eess.SP": "Signal Processing",
    "eess.SY": "Systems and Control",
    # Mathematics (common)
    "math.AG": "Algebraic Geometry",
    "math.AT": "Algebraic Topology",
    "math.CO": "Combinatorics",
    "math.DS": "Dynamical Systems",
    "math.LO": "Logic",
    "math.NA": "Numerical Analysis",
    "math.OC": "Optimization and Control",
    "math.PR": "Probability",
    "math.ST": "Statistics Theory",
    # Physics (selected)
    "astro-ph.GA": "Astrophysics of Galaxies",
    "cond-mat.stat-mech": "Statistical Mechanics",
    "gr-qc": "General Relativity and Quantum Cosmology",
    "hep-th": "High Energy Physics - Theory",
    "quant-ph": "Quantum Physics",
    "physics.comp-ph": "Computational Physics",
    # Quantitative Biology / Finance / Economics
    "q-bio.BM": "Biomolecules",
    "q-bio.NC": "Neurons and Cognition",
    "q-fin.CP": "Computational Finance",
    "q-fin.PM": "Portfolio Management",
    "q-fin.TR": "Trading and Market Microstructure",
    "q-fin.ST": "Statistical Finance",
    "econ.EM": "Econometrics",
}


def lookup_category(cat: str) -> str | None:
    return TAXONOMY.get(cat)
