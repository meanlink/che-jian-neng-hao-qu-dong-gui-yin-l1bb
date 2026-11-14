from pydantic import BaseModel, Field, field_validator
from datetime import date


def _iso_day(value: str) -> str:
    try:
        date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('日期须为 YYYY-MM-DD')
    return value


class LedgerAccountInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    job_id: int
    line_code: str = Field(min_length=1, max_length=60)
    line_name: str = Field(default='', max_length=120)


class LedgerRowInput(BaseModel):
    day: str
    actual_kwh: float

    @field_validator('day')
    @classmethod
    def _day(cls, v):
        return _iso_day(v)

    @field_validator('actual_kwh')
    @classmethod
    def _kwh(cls, v):
        if v != v or v in (float('inf'), float('-inf')):
            raise ValueError('实测电耗必须为有限数值')
        if not 0 <= v <= 1_000_000_000:
            raise ValueError('日电耗须介于 0 和 10 亿 kWh')
        return v


class LedgerBatchInput(BaseModel):
    idem_key: str = Field(min_length=4, max_length=80)
    rows: list[LedgerRowInput] = Field(min_length=1, max_length=2000)

    @field_validator('idem_key')
    @classmethod
    def _idem(cls, v):
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError('幂等键仅允许字母、数字、-、_')
        return v


class LedgerReversalInput(BaseModel):
    idem_key: str = Field(min_length=4, max_length=80)
    ref_day: str
    actual_kwh: float
    note: str = Field(default='', max_length=300)

    @field_validator('idem_key')
    @classmethod
    def _idem(cls, v):
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError('幂等键仅允许字母、数字、-、_')
        return v

    @field_validator('ref_day')
    @classmethod
    def _day(cls, v):
        return _iso_day(v)

    @field_validator('actual_kwh')
    @classmethod
    def _kwh(cls, v):
        if v != v or v in (float('inf'), float('-inf')):
            raise ValueError('实测电耗必须为有限数值')
        if not 0 <= v <= 1_000_000_000:
            raise ValueError('日电耗须介于 0 和 10 亿 kWh')
        return v


class LedgerMonthInput(BaseModel):
    month: str

    @field_validator('month')
    @classmethod
    def _month(cls, v):
        try:
            date.fromisoformat(v + '-01')
        except (ValueError, TypeError):
            raise ValueError('月份须为 YYYY-MM')
        return v
