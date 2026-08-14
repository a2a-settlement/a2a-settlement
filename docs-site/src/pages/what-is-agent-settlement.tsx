import React, {useEffect, type ReactNode} from 'react';
import Layout from '@theme/Layout';

export default function WhatIsAgentSettlementAlias(): ReactNode {
  useEffect(() => {
    window.location.replace('/docs/agent-settlement/');
  }, []);
  return (
    <Layout title="What is Agent Settlement?">
      <main className="container margin-vert--xl">
        <p>
          Redirecting to{' '}
          <a href="/docs/agent-settlement/">What is Agent Settlement?</a>…
        </p>
      </main>
    </Layout>
  );
}
