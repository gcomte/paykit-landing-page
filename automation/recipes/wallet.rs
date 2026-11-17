use paykit_lib::{PaykitAppId, PaymentEndpointIdentifier, PaymentEndpointPayload, PaymentList};

// This is wallet policy, not an automatic Paykit rail selector.
fn choose<'a>(list: &PaymentList, supported: &[&'a str]) -> Option<&'a str> {
    supported.iter().find_map(|id| {
        let identifier = PaymentEndpointIdentifier::new(*id).ok()?;
        list.payment_endpoints.get(&identifier).map(|_| *id)
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let merchant_app = PaykitAppId::new("demo-pos")?;
    let mut discovered = PaymentList::default();
    discovered.payment_endpoints.insert(
        PaymentEndpointIdentifier::new("btc-lightning-bolt11")?,
        PaymentEndpointPayload::new("fixture-only:not-a-payable-invoice"),
    );
    assert!(PaymentEndpointIdentifier::new("../private").is_err());
    assert_eq!(choose(&discovered, &["btc-lightning-bolt11"]), Some("btc-lightning-bolt11"));
    assert!(choose(&discovered, &["unsupported-rail"]).is_none());
    println!("{}: shared rail btc-lightning-bolt11; unsupported rail: no shared rail", merchant_app.as_str());
    println!("Local fixture only; no discovery request or payment was sent.");
    Ok(())
}
