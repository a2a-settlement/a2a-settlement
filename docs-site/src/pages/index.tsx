import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import Heading from '@theme/Heading';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className="container">
        <Heading as="h1" className="hero__title">
          {siteConfig.title}
        </Heading>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <p style={{opacity: 0.9, maxWidth: 640, margin: '0 auto 1.5rem'}}>
          A2A-SE is an open standard for the settlement layer of autonomous agent commerce.
        </p>
        <div className={styles.buttons} style={{gap: '1rem', flexWrap: 'wrap'}}>
          <Link
            className="button button--secondary button--lg"
            to="/docs/agent-settlement/">
            What is Agent Settlement?
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            href="https://sandbox.a2a-settlement.org">
            Run a Settlement
          </Link>
        </div>
        <p style={{marginTop: '1.25rem', fontSize: '0.9rem', opacity: 0.85}}>
          A2A-SE standard docs — SettleBridge product docs live at{' '}
          <a href="https://settlebridge.ai" style={{color: 'inherit', textDecoration: 'underline'}}>
            settlebridge.ai
          </a>
          .
        </p>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Agent Settlement — A2A-SE"
      description="A2A-SE is an open standard for the settlement layer of autonomous agent commerce. Payments move value; authorization permits spend; settlement decides finality.">
      <HomepageHeader />
      <main>
        <HomepageFeatures />
      </main>
    </Layout>
  );
}
