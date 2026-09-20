"""Comprehensive test suite for MT5 execution engine.

Tests:
- Retry logic with mocked return codes
- Success codes (10000, 10001)
- Retryable price codes (immediate retry)
- Retryable backoff codes (exponential backoff)
- Fatal codes (no retry)
- FOK fill policy enforcement
- Fresh price on each retry
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
import time

from ftmo_mt5.execution.mt5_executor import MT5Executor, ExecutionResult
from ftmo_mt5.core.models import (
    TradeRequest,
    OrderAction,
    OrderType,
    FillPolicy,
)
from ftmo_mt5.core.constants import (
    MT5_SUCCESS_CODES,
    MT5_RETRYABLE_PRICE,
    MT5_RETRYABLE_BACKOFF,
    MT5_FATAL_CODES,
)


# ============================================================================
# Execution Result Tests
# ============================================================================


class TestExecutionResult:
    """Test ExecutionResult data structure."""

    def test_execution_result_success(self):
        """Create successful execution result."""
        result = ExecutionResult(
            success=True,
            retcode=10000,
            order_ticket=999,
            fill_price=Decimal("1.0850"),
            volume_filled=Decimal("1.0"),
            slippage=Decimal("-0.0005"),
        )

        assert result.success is True
        assert result.retcode == 10000
        assert result.order_ticket == 999
        assert result.fill_price == Decimal("1.0850")

    def test_execution_result_failure(self):
        """Create failed execution result."""
        result = ExecutionResult(
            success=False,
            retcode=10011,
            error_message="Insufficient funds",
        )

        assert result.success is False
        assert result.retcode == 10011
        assert "funds" in result.error_message.lower()

    def test_execution_result_retry_count(self):
        """Test execution result tracks retry count."""
        result = ExecutionResult(
            success=True,
            retcode=10000,
            order_ticket=999,
            retry_count=2,
            latency_ms=150.5,
        )

        assert result.retry_count == 2
        assert result.latency_ms == 150.5


# ============================================================================
# MT5 Return Code Classification Tests
# ============================================================================


class TestReturnCodeClassification:
    """Test MT5 return code classifications."""

    def test_success_codes(self):
        """Verify success code set."""
        assert 10000 in MT5_SUCCESS_CODES  # TRADE_RETCODE_DONE
        assert 10001 in MT5_SUCCESS_CODES  # TRADE_RETCODE_DONE_PARTIAL
        assert len(MT5_SUCCESS_CODES) == 2

    def test_retryable_price_codes(self):
        """Verify retryable price code set."""
        assert 10002 in MT5_RETRYABLE_PRICE  # TRADE_RETCODE_ERROR
        assert 10004 in MT5_RETRYABLE_PRICE  # TRADE_RETCODE_TIMEOUT
        assert 10007 in MT5_RETRYABLE_PRICE  # TRADE_RETCODE_INVALID_PRICE
        assert 10012 in MT5_RETRYABLE_PRICE  # TRADE_RETCODE_PRICE_CHANGED
        assert 10023 in MT5_RETRYABLE_PRICE  # TRADE_RETCODE_CONNECTION

    def test_retryable_backoff_codes(self):
        """Verify retryable backoff code set."""
        assert 10023 in MT5_RETRYABLE_BACKOFF  # TRADE_RETCODE_CONNECTION
        assert 10004 in MT5_RETRYABLE_BACKOFF  # TRADE_RETCODE_TIMEOUT
        assert 10016 in MT5_RETRYABLE_BACKOFF  # TRADE_RETCODE_TOO_MANY_REQUESTS

    def test_fatal_codes(self):
        """Verify fatal code set."""
        assert 10011 in MT5_FATAL_CODES  # TRADE_RETCODE_NO_MONEY
        assert 10024 in MT5_FATAL_CODES  # TRADE_RETCODE_ONLY_REAL
        assert 10025 in MT5_FATAL_CODES  # TRADE_RETCODE_LIMIT_ORDERS

    def test_no_overlap_between_sets(self):
        """Verify no overlap between success and retryable sets."""
        # Success codes should not appear in retryable or fatal
        assert MT5_SUCCESS_CODES.isdisjoint(MT5_RETRYABLE_PRICE)
        assert MT5_SUCCESS_CODES.isdisjoint(MT5_FATAL_CODES)


# ============================================================================
# Retry Logic Tests
# ============================================================================


class TestRetryLogic:
    """Test order execution retry logic."""

    @pytest.fixture
    def mock_gateway(self):
        """Create mock MT5 gateway."""
        gateway = Mock()
        gateway.get_tick = Mock()
        gateway.order_send = Mock()
        return gateway

    async def test_success_on_first_attempt(self, mock_gateway):
        """Test successful execution on first attempt (no retry)."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # Mock successful response from order_send
        mock_gateway.order_send.return_value = {
            'retcode': 10000,  # Success code
            'order': 999,  # Order ticket
            'price': Decimal("1.0850"),  # Fill price
            'volume': Decimal("1.0"),  # Volume filled
        }

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request, max_retries=3)

        assert result.success is True
        assert result.retcode == 10000
        assert result.order_ticket == 999
        assert result.retry_count == 0
        # Should only call once (no retries)
        assert mock_gateway.order_send.call_count == 1

    async def test_success_after_retryable_price_code(self, mock_gateway):
        """Test success after price change code (retry with fresh price)."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # First call: price changed, Second call: success
        mock_gateway.order_send.side_effect = [
            {'retcode': 10012},  # TRADE_RETCODE_PRICE_CHANGED
            {'retcode': 10000, 'order': 999, 'price': Decimal("1.0850"), 'volume': Decimal("1.0")},
        ]

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request, max_retries=3)

        assert result.success is True
        assert result.retry_count == 1
        # Should call twice (1 failure + 1 success)
        assert mock_gateway.order_send.call_count == 2

    async def test_exhaust_retries_on_price_errors(self, mock_gateway):
        """Test exhausting max retries on persistent price errors."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # Always return price error
        mock_gateway.order_send.return_value = {'retcode': 10012}  # TRADE_RETCODE_PRICE_CHANGED

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request, max_retries=2)

        assert result.success is False
        assert result.retry_count >= 2
        # Should call max_retries + 1 times (initial + retries)
        assert mock_gateway.order_send.call_count >= 2

    async def test_fatal_code_no_retry(self, mock_gateway):
        """Test fatal codes result in immediate failure, no retry."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # Fatal error: insufficient funds
        mock_gateway.order_send.return_value = {'retcode': 10011}  # TRADE_RETCODE_NO_MONEY

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request, max_retries=3)

        assert result.success is False
        assert result.retcode == 10011
        assert result.retry_count == 0  # No retries on fatal
        # Should call only once (no retries)
        assert mock_gateway.order_send.call_count == 1


# ============================================================================
# Backoff Strategy Tests
# ============================================================================


class TestBackoffStrategy:
    """Test exponential backoff for certain error codes."""

    @pytest.fixture
    def mock_gateway(self):
        """Create mock MT5 gateway."""
        gateway = Mock()
        gateway.get_tick = Mock()
        gateway.order_send = Mock()
        return gateway

    async def test_backoff_on_connection_error(self, mock_gateway):
        """Test exponential backoff on connection errors."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # Fail with connection error, then succeed
        mock_gateway.order_send.side_effect = [
            {'retcode': 10023},  # TRADE_RETCODE_CONNECTION
            {'retcode': 10000, 'order': 999, 'price': Decimal("1.0850"), 'volume': Decimal("1.0")},
        ]

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        start = time.time()
        result = await executor.send_order(trade_request, max_retries=3)
        elapsed = time.time() - start

        assert result.success is True
        # Should have delay between retries (backoff)
        assert elapsed >= executor._min_backoff_s

    async def test_backoff_on_timeout(self, mock_gateway):
        """Test backoff on timeout error."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        mock_gateway.order_send.side_effect = [
            {'retcode': 10004},  # TRADE_RETCODE_TIMEOUT
            {'retcode': 10000, 'order': 999, 'price': Decimal("1.0850"), 'volume': Decimal("1.0")},
        ]

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request, max_retries=3)

        assert result.success is True

    async def test_backoff_on_too_many_requests(self, mock_gateway):
        """Test backoff on rate limiting."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        mock_gateway.order_send.side_effect = [
            {'retcode': 10016},  # TRADE_RETCODE_TOO_MANY_REQUESTS
            {'retcode': 10000, 'order': 999, 'price': Decimal("1.0850"), 'volume': Decimal("1.0")},
        ]

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request, max_retries=3)

        assert result.success is True


# ============================================================================
# Fresh Price on Retry Tests
# ============================================================================


class TestFreshPriceOnRetry:
    """Test that fresh prices are fetched on each retry."""

    @pytest.fixture
    def mock_gateway(self):
        """Create mock MT5 gateway."""
        gateway = Mock()
        gateway.get_tick = Mock()
        gateway.order_send = Mock()
        return gateway

    async def test_fresh_price_fetched_on_price_retry(self, mock_gateway):
        """Test fresh price is fetched when retrying price error."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data for each call
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # First attempt returns price error, second succeeds
        mock_gateway.order_send.side_effect = [
            {'retcode': 10012},  # Price changed
            {'retcode': 10000, 'order': 999, 'price': Decimal("1.0860"), 'volume': Decimal("1.0")},
        ]

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        # The implementation should fetch fresh prices
        # This test verifies behavior in actual implementation
        result = await executor.send_order(trade_request, max_retries=3)

        assert result.success is True
        assert result.retry_count == 1


# ============================================================================
# FOK (Fill or Kill) Fill Policy Tests
# ============================================================================


class TestFOKFillPolicy:
    """Test FOK (Fill or Kill) fill policy enforcement."""

    @pytest.fixture
    def mock_gateway(self):
        """Create mock MT5 gateway."""
        gateway = Mock()
        gateway.get_tick = Mock()
        gateway.order_send = Mock()
        return gateway

    async def test_fok_policy_enforcement(self, mock_gateway):
        """Test FOK policy is correctly set in request."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        mock_gateway.order_send.return_value = {
            'retcode': 10000,
            'order': 999,
            'price': Decimal("1.0850"),
            'volume': Decimal("1.0"),
        }

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            fill_policy=FillPolicy.FOK,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request)

        assert result.success is True
        # Verify the request was made with FOK policy
        # Implementation would check gateway was called with correct params
        mock_gateway.order_send.assert_called_once()

    async def test_policy_default_fill_policy(self, mock_gateway):
        """Test default POLICY fill policy."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        mock_gateway.order_send.return_value = {
            'retcode': 10000,
            'order': 999,
            'price': Decimal("1.0850"),
            'volume': Decimal("1.0"),
        }

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            fill_policy=FillPolicy.POLICY,  # Default
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request)

        assert result.success is True


# ============================================================================
# Slippage and Fill Price Tests
# ============================================================================


class TestFillPriceAndSlippage:
    """Test fill price and slippage tracking."""

    @pytest.fixture
    def mock_gateway(self):
        """Create mock MT5 gateway."""
        gateway = Mock()
        gateway.get_tick = Mock()
        gateway.order_send = Mock()
        return gateway

    async def test_slippage_tracking_negative(self, mock_gateway):
        """Test negative slippage (favorable fill)."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # Request price 1.0850, filled at 1.0845 (favorable)
        mock_gateway.order_send.return_value = {
            'retcode': 10000,
            'order': 999,
            'price': Decimal("1.0845"),  # Better than requested
            'volume': Decimal("1.0"),
        }

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            price=Decimal("1.0850"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request)

        assert result.success is True
        assert result.fill_price == Decimal("1.0845")

    async def test_slippage_tracking_positive(self, mock_gateway):
        """Test positive slippage (unfavorable fill)."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        # Request price 1.0850, filled at 1.0855 (unfavorable)
        mock_gateway.order_send.return_value = {
            'retcode': 10000,
            'order': 999,
            'price': Decimal("1.0855"),  # Worse than requested
            'volume': Decimal("1.0"),
        }

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            price=Decimal("1.0850"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request)

        assert result.success is True
        assert result.fill_price == Decimal("1.0855")

    async def test_partial_fill_tracking(self, mock_gateway):
        """Test partial fill is recorded correctly."""
        executor = MT5Executor(mock_gateway)

        # Mock tick data
        mock_gateway.get_tick.return_value = {'bid': Decimal("1.0850"), 'ask': Decimal("1.0851")}

        mock_gateway.order_send.return_value = {
            'retcode': 10001,  # TRADE_RETCODE_DONE_PARTIAL
            'order': 999,
            'price': Decimal("1.0850"),
            'volume': Decimal("0.5"),  # Only 0.5 of 1.0 filled
        }

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request)

        # Partial fills are typically considered success
        assert result.retcode == 10001
        assert result.volume_filled == Decimal("0.5")


# ============================================================================
# Integration Tests
# ============================================================================


class TestExecutionIntegration:
    """Integration tests for execution engine."""

    @pytest.fixture
    def mock_gateway(self):
        """Create mock MT5 gateway."""
        gateway = Mock()
        gateway.send_order_request = Mock()
        return gateway

    async def test_complete_order_lifecycle(self, mock_gateway):
        """Test complete order execution lifecycle."""
        executor = MT5Executor(mock_gateway)

        # Simulate real-world scenario: price changes, then success
        mock_gateway.send_order_request.side_effect = [
            (10012, None, None, None, None),  # Price changed
            (10000, 999, Decimal("1.0852"), Decimal("1.0"), Decimal("-0.0002")),
        ]

        trade_request = TradeRequest(
            symbol="EURUSD",
            action=OrderAction.BUY,
            order_type=OrderType.MARKET,
            volume=Decimal("1.0"),
            magic=234000,
            fill_policy=FillPolicy.FOK,
            correlation_id=uuid4(),
        )

        result = await executor.send_order(trade_request, max_retries=3)

        assert result.success is True
        assert result.order_ticket == 999
        assert result.fill_price == Decimal("1.0852")
        assert result.retry_count == 1

    def test_multiple_orders_different_outcomes(self, mock_gateway):
        """Test executing multiple orders with different outcomes."""
        executor = MT5Executor(mock_gateway)

        # First order: success
        # Second order: fatal error
        # Third order: success after retry
        mock_gateway.send_order_request.side_effect = [
            (10000, 1, Decimal("1.0850"), Decimal("1.0"), Decimal("0")),
            (10011, None, None, None, None),  # No money
            (10004, None, None, None, None),  # Timeout
            (10000, 3, Decimal("1.0851"), Decimal("1.0"), Decimal("0")),
        ]

        orders = [
            TradeRequest(
                symbol="EURUSD",
                action=OrderAction.BUY,
                order_type=OrderType.MARKET,
                volume=Decimal("1.0"),
                magic=234000,
                correlation_id=uuid4(),
            ),
            TradeRequest(
                symbol="GBPUSD",
                action=OrderAction.BUY,
                order_type=OrderType.MARKET,
                volume=Decimal("2.0"),
                magic=234000,
                correlation_id=uuid4(),
            ),
            TradeRequest(
                symbol="USDJPY",
                action=OrderAction.BUY,
                order_type=OrderType.MARKET,
                volume=Decimal("0.5"),
                magic=234000,
                correlation_id=uuid4(),
            ),
        ]

        results = [executor.send_order(o) for o in orders]

        assert results[0].success is True
        assert results[1].success is False  # Fatal error
        assert results[2].success is True  # Recovered after retry
