from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class LedgerAccount(Base):
    """考核账本开户记录：线体、批准结果与预测依据在开户时固定，后续不随任务变化。"""
    __tablename__ = 'ledger_accounts'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    line_code: Mapped[str] = mapped_column(String(60))
    line_name: Mapped[str] = mapped_column(String(120), default='')
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    # 冻结的归因版本与预测依据（job 结果快照、留出期逐日预测、参数与指标）
    basis_version: Mapped[str] = mapped_column(String(40))
    basis: Mapped[dict] = mapped_column(JSON, default=dict)
    basis_checksum: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('job_id', 'line_code', name='uk_ledger_account_job_line'),)


class LedgerBatch(Base):
    """入账批次：冲突整批拒绝；幂等键保证中断重试返回同一结果且不重复累计。"""
    __tablename__ = 'ledger_batches'
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('ledger_accounts.id'))
    idem_key: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default='entry')  # entry / reversal
    status: Mapped[str] = mapped_column(String(20), default='pending')  # pending/posted/rejected
    note: Mapped[str] = mapped_column(String(300), default='')
    reject_reason: Mapped[str] = mapped_column(String(500), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    posted_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    __table_args__ = (UniqueConstraint('account_id', 'idem_key', name='uk_ledger_batch_account_idem'),)


class LedgerEntry(Base):
    """考核日账：按日期登记预计、实测电耗与差额；冲正以独立行登记并回链原始日账。"""
    __tablename__ = 'ledger_entries'
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('ledger_accounts.id'))
    batch_id: Mapped[int] = mapped_column(ForeignKey('ledger_batches.id'))
    entry_type: Mapped[str] = mapped_column(String(20), default='original')  # original / reversal
    status: Mapped[str] = mapped_column(String(20), default='pending')  # pending/posted
    # original: 抄表业务日期；reversal: 冲正登记日期（落在未封月份）
    day: Mapped[str] = mapped_column(String(10))
    # 冲正指向的原始业务日期与原始日账
    ref_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey('ledger_entries.id'), nullable=True)
    predicted_kwh: Mapped[float | None] = mapped_column(Double, nullable=True)
    actual_kwh: Mapped[float] = mapped_column(Double)
    variance_kwh: Mapped[float | None] = mapped_column(Double, nullable=True)
    book_month: Mapped[str] = mapped_column(String(7))  # YYYY-MM，入账归属月份
    created_at: Mapped[float] = mapped_column(Double)
    posted_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    __table_args__ = (
        # 同一账户同一业务日期只允许一条原始日账（重复入账不重复累计）；
        # 冲正行按登记日落行，与原始日账互不阻塞
        UniqueConstraint('account_id', 'day', 'entry_type', name='uk_ledger_account_day_type'),
        # 每条原始日账最多一条冲正；重试冲正幂等
        UniqueConstraint('reversal_of_id', name='uk_ledger_reversal_of'),
        Index('ix_ledger_account_month', 'account_id', 'book_month'),
        Index('ix_ledger_batch', 'batch_id'),
    )


class LedgerMonth(Base):
    """月度封账：封账生效后快照原值，迟到与更正只能在未封月份以冲正登记。"""
    __tablename__ = 'ledger_months'
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('ledger_accounts.id'))
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    sealed: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_count: Mapped[int] = mapped_column(default=0)
    reversal_count: Mapped[int] = mapped_column(default=0)
    predicted_total: Mapped[float] = mapped_column(Double, default=0.0)
    actual_total: Mapped[float] = mapped_column(Double, default=0.0)
    variance_total: Mapped[float] = mapped_column(Double, default=0.0)
    basis_version: Mapped[str] = mapped_column(String(40), default='')
    sealed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    sealed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    __table_args__ = (UniqueConstraint('account_id', 'month', name='uk_ledger_account_month'),)
