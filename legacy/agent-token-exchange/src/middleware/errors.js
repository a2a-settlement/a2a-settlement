function errorHandler(err, req, res, next) {
  console.error(`[ERROR] ${err.name}: ${err.message}`);

  if (err.name === 'ExchangeError') {
    return res.status(err.statusCode).json({ error: err.message });
  }

  // PostgreSQL constraint violations
  if (err.code === '23505') {
    return res.status(409).json({ error: 'Resource already exists' });
  }
  if (err.code === '23503') {
    return res.status(400).json({ error: 'Referenced resource not found' });
  }
  if (err.code === '23514') {
    return res.status(400).json({ error: 'Constraint violation - check your values' });
  }

  // Default
  return res.status(500).json({
    error: process.env.NODE_ENV === 'production'
      ? 'Internal server error'
      : err.message,
  });
}

module.exports = { errorHandler };
