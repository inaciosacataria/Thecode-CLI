from enum import IntEnum


class RiskLevel(IntEnum):
    READ_ONLY = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

